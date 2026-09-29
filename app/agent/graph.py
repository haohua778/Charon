"""One fixed LangGraph DAG, with native dispatch and runtime dependency injection."""
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.nodes import (
    aggregate_node, assess_node, code_checks_node, dispatch_assessments,
    dispatch_verifications, evidence_node, extract_node, fixed_node, load_node,
    merge_verifications, record_node, segment_node, verify_dimension_node,
)
from app.agent.state import ReviewContext, ReviewState, VerificationState
from app.core.config import Settings
from app.core.errors import CharonError
from app.llm.client import LLM
from app.schemas import FixedInput, ReviewRecord, ReviewRequest


def route_preparation(state: ReviewState) -> Literal['load_fixed_input', 'extract_claims']:
    return 'load_fixed_input' if state['fixed_input'] is not None else 'extract_claims'


def after_extract(state: ReviewState) -> Literal['write_record', 'link_evidence']:
    return 'write_record' if state['preparation_error'] else 'link_evidence'


def after_evidence(state: ReviewState) -> Literal['write_record', 'code_checks']:
    return 'write_record' if state['preparation_error'] else 'code_checks'


def after_aggregate(state: ReviewState) -> Literal['verify_flags', 'write_record']:
    return ('verify_flags' if state['request'].verify_enabled and any(flag.origin == 'model' for flag in state['flags'])
            else 'write_record')


verification_builder = StateGraph(VerificationState, context_schema=ReviewContext)
verification_builder.add_node('verify_dispatch', lambda state: {})
verification_builder.add_node('verify_dimension', verify_dimension_node)
verification_builder.add_node('verify_merge', merge_verifications)
verification_builder.add_edge(START, 'verify_dispatch')
verification_builder.add_conditional_edges('verify_dispatch', dispatch_verifications, ['verify_dimension'])
verification_builder.add_edge('verify_dimension', 'verify_merge')
verification_builder.add_edge('verify_merge', END)
verification_graph = verification_builder.compile()


async def verify_node(state: ReviewState, runtime: Runtime[ReviewContext]) -> dict[str, Any]:
    """The parent graph sees exactly one verification node and no loop."""
    result = await verification_graph.ainvoke({
        'request': state['request'], 'rubric': state['rubric'], 'units': state['units'],
        'flags': state['flags'], 'verification_batches': [],
    }, context=runtime.context)
    return {key: result[key] for key in ('flags', 'verification_status', 'verification_error_code')}


builder = StateGraph(ReviewState, context_schema=ReviewContext)
for name, node in (
    ('load_rubric', load_node), ('segment', segment_node), ('extract_claims', extract_node),
    ('link_evidence', evidence_node), ('load_fixed_input', fixed_node), ('code_checks', code_checks_node),
    ('assess_dimension', assess_node), ('aggregate', aggregate_node), ('verify_flags', verify_node),
    ('write_record', record_node),
):
    builder.add_node(name, node)
builder.add_edge(START, 'load_rubric')
builder.add_edge('load_rubric', 'segment')
builder.add_conditional_edges('segment', route_preparation)
builder.add_conditional_edges('extract_claims', after_extract)
builder.add_conditional_edges('link_evidence', after_evidence)
builder.add_conditional_edges('load_fixed_input', after_evidence)
builder.add_conditional_edges('code_checks', dispatch_assessments, ['assess_dimension', 'aggregate'])
builder.add_edge('assess_dimension', 'aggregate')
builder.add_conditional_edges('aggregate', after_aggregate)
builder.add_edge('verify_flags', 'write_record')
builder.add_edge('write_record', END)
graph = builder.compile()


async def run_review(request: ReviewRequest, *, llm: LLM, settings: Settings,
                     fixed_input: FixedInput | None = None) -> ReviewRecord:
    if llm.usage.calls != 0:
        raise CharonError('llm_already_used')
    review_id = str(uuid4())
    state = await graph.ainvoke({
        'request': request, 'fixed_input': fixed_input, 'review_id': review_id,
        'started_at': perf_counter(), 'dimension_results': [],
    }, config={'configurable': {'thread_id': review_id}}, context=ReviewContext(llm=llm, settings=settings))
    return state['record']
