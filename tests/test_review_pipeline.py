import asyncio
import json

import pytest

from app.core.errors import CharonError
from app.llm.fake import FakeLLM, demo_response
from app.schemas import FixedInput, ReviewRequest

REPORT = 'Training drove growth.\n\nThe budget states 60 + 50 = 100.'


def assert_assessment_check_fields(payload):
    expected = {'check_id', 'description', 'questions', 'examples', 'anchor_field', 'target_ids', 'tools'}
    for check in payload['checks']:
        assert set(check) == expected | ({'standard'} if 'standard' in check else set())



def review(request, settings, llm, fixed_input=None):
    from app.agent.graph import run_review

    return asyncio.run(run_review(request, settings=settings, llm=llm, fixed_input=fixed_input))


@pytest.mark.parametrize('fail_second', [False, True])
def test_union_confidence_and_failed_dimension_invalidates_whole_run(settings, flagging_responder, fail_second):
    def repeated(stage, payload):
        if stage == 'assess' and payload['run_index'] == 1 and payload['dimension'] == 'interpretation':
            if fail_second:
                raise CharonError('upstream_error')
            return demo_response(stage, payload)
        return flagging_responder(stage, payload)

    record = review(ReviewRequest(report=REPORT, runs=2), settings, FakeLLM(repeated))
    assert record.status == ('partial' if fail_second else 'complete')
    assert record.valid_runs == (1 if fail_second else 2)
    model, = [flag for flag in record.flags if flag.origin == 'model']
    assert model.occurrences == 1
    assert model.confidence == (None if fail_second else .5)
    code, = [flag for flag in record.flags if flag.origin == 'code']
    assert code.confidence is None and code.occurrences is None and code.verification is None
    assert [flag.flag_id for flag in record.flags] == ['F1', 'F2']
    assert len(record.flags) == len({flag.key for flag in record.flags})


def test_fixed_input_skips_preparation_and_checks_exact_source_text(settings, flagging_responder):
    baseline = review(ReviewRequest(report=REPORT), settings, FakeLLM(flagging_responder))
    assert baseline.status == 'complete'
    fixed = FixedInput(claims=baseline.claims, evidence=baseline.evidence)
    stages = []

    def checks_only(stage, payload):
        stages.append(stage)
        assert stage not in ('extract', 'evidence')
        return flagging_responder(stage, payload)

    reused = review(ReviewRequest(report=REPORT), settings, FakeLLM(checks_only), fixed)
    assert reused.status == 'complete' and reused.mode == 'fixed'
    assert reused.usage.calls == baseline.usage.calls - 2
    assert {flag.key for flag in reused.flags} == {flag.key for flag in baseline.flags}
    tampered = fixed.model_copy(deep=True)
    tampered.claims[0].text = 'INVENTED_CLAIM'
    rejected = review(ReviewRequest(report=REPORT), settings, FakeLLM(), tampered)
    assert rejected.status == 'failed' and rejected.error_code == 'invalid_fixed_input'
    assert rejected.usage.calls == 0 and rejected.flags == []
    changed_report = review(ReviewRequest(report=REPORT.replace('growth', 'decline')), settings, FakeLLM(), fixed)
    assert changed_report.status == 'failed' and changed_report.error_code == 'invalid_fixed_input'


@pytest.mark.parametrize('ranges', [
    [{'first': 'p1.s1', 'last': 'p2.s1'}],
    [{'first': 'p1.s2', 'last': 'p1.s1'}],
    [{'first': 'p1.s1', 'last': 'p1.s2'}, {'first': 'p1.s2', 'last': 'p1.s2'}],
    [{'first': 'p3.s1', 'last': 'p3.s1'}],
    [{'first': 'p4', 'last': 'p4'}],
])
def test_invalid_extraction_repairs_once_and_bypasses_assessment(settings, ranges):
    report = 'First sentence. Second sentence.\n\nThird sentence.\n\n| A | B |\n\n# Heading'
    stages = []

    def invalid(stage, payload):
        stages.append(stage)
        assert stage == 'extract'
        return {'claims': ranges}

    record = review(ReviewRequest(report=report), settings, FakeLLM(invalid))
    assert record.status == 'failed' and record.error_code == 'invalid_claim_target'
    assert stages == ['extract', 'extract'] and record.runs == []


def test_empty_extraction_still_runs_code_checks_and_lists_standards_separately(settings):
    stages = []

    def empty(stage, payload):
        stages.append(stage)
        if stage == 'extract':
            return {'claims': []}
        if stage == 'assess':
            assert all(not target_id.startswith('p') for check in payload['checks'] for target_id in check['target_ids'])
        return demo_response(stage, payload)

    record = review(ReviewRequest(report=REPORT), settings, FakeLLM(empty))
    assert record.status == 'complete' and 'evidence' not in stages
    assert record.claims == [] and record.evidence == []
    code, = record.flags
    assert code.origin == 'code' and code.claim_id is None
    assert {item.check_id for item in record.pending_standards} == {'metric_matches_standard', 'escalation_required'}
    assert not set(record.unchecked_check_ids).intersection(item.check_id for item in record.pending_standards)
    assert 'causal_language' in record.unchecked_check_ids


def test_verification_is_once_per_dimension_and_only_adds_verification(settings, flagging_responder):
    stages = []

    def observed(stage, payload):
        stages.append(stage)
        if stage == 'verify':
            assert len(payload['flags']) == 1
            assert payload['flags'][0]['check_id'] == 'causal_language'
        return flagging_responder(stage, payload)

    enabled = review(ReviewRequest(report=REPORT, runs=3), settings, FakeLLM(observed))
    assert enabled.status == 'complete' and enabled.verification_status == 'complete'
    assert stages.count('verify') == 1
    stages.clear()
    disabled = review(ReviewRequest(report=REPORT, runs=3, verify_enabled=False), settings, FakeLLM(observed))
    assert disabled.verification_status == 'not_run' and 'verify' not in stages
    assert [flag.model_dump(exclude={'verification'}) for flag in enabled.flags] == [
        flag.model_dump(exclude={'verification'}) for flag in disabled.flags]
    assert all(flag.verification is None for flag in disabled.flags)


def test_verification_missing_rows_repairs_once_but_preserves_review(settings, flagging_responder):
    attempts = []

    def broken(stage, payload):
        if stage == 'verify':
            attempts.append(payload)
            return {'verifications': []}
        return flagging_responder(stage, payload)

    record = review(ReviewRequest(report=REPORT), settings, FakeLLM(broken))
    assert record.status == 'complete' and record.verification_status == 'failed'
    assert record.verification_error_code == 'invalid_verification_coverage'
    assert len(attempts) == 2 and 'repair' in attempts[1]
    assert all(flag.verification is None for flag in record.flags)


def test_assessment_coverage_can_repair_once(minimal_settings, flagging_responder):
    attempts = []

    def initially_missing(stage, payload):
        if stage == 'assess':
            attempts.append(payload)
            if 'repair' not in payload:
                return {'assessments': []}
        return flagging_responder(stage, payload)

    record = review(ReviewRequest(report=REPORT, verify_enabled=False), minimal_settings, FakeLLM(initially_missing))
    assert record.status == 'complete' and len(attempts) == 2
    assert attempts[1]['repair']['previous_output'] == {'assessments': []}
    assert attempts[1]['repair']['errors']


def test_tool_round_is_bounded_and_uses_shared_budget(minimal_settings, flagging_responder):
    for path in minimal_settings.rubrics_dir.glob('*.json'):
        rubric = json.loads(path.read_text())
        rubric['dimensions'][0]['checks'][0]['tools'] = ['calculator']
        path.write_text(json.dumps(rubric))
    stages = []

    def request_tool(stage, payload):
        stages.append(stage)
        if stage in ('assess', 'assess_final'):
            assert_assessment_check_fields(payload)
        if stage == 'assess':
            return {'assessments': [], 'tool_requests': [
                {'check_id': 'causal_language', 'name': 'calculator', 'expression': '60 + 50'},
            ]}
        if stage == 'assess_final':
            assert payload['tool_results'][0]['result'] == 110
        return flagging_responder(stage, payload)

    complete = review(ReviewRequest(report=REPORT, verify_enabled=False), minimal_settings, FakeLLM(request_tool))
    assert complete.status == 'complete' and stages.count('assess_final') == 1
    disabled = review(ReviewRequest(report=REPORT, tools_enabled=False), minimal_settings, FakeLLM(request_tool))
    assert disabled.status == 'failed' and disabled.error_code == 'tool_not_allowed'
    exhausted = review(ReviewRequest(report=REPORT), minimal_settings, FakeLLM(max_calls=2))
    assert exhausted.status == 'failed' and exhausted.error_code == 'call_budget_exceeded'
    assert exhausted.usage.calls == 2

    def endless(stage, payload):
        return request_tool('assess', payload) if stage == 'assess_final' else request_tool(stage, payload)

    limited = review(ReviewRequest(report=REPORT), minimal_settings, FakeLLM(endless))
    assert limited.status == 'failed' and limited.error_code == 'tool_round_limit'


def test_calculator_rejects_executable_and_unbounded_input():
    from app.tools.calculator import calculate

    assert calculate('(60 + 50) / 2') == 55
    for expression in ("__import__('os').system('id')", '(1).__class__', '1 / 0',
                       '2 ** 1000000', '[1]', '1e309', '9' * 200):
        with pytest.raises(CharonError, match='^invalid_tool_expression$'):
            calculate(expression)


def test_graph_structure_and_runtime_state_match_fixed_dag():
    from app.agent.graph import graph
    from app.agent.state import ReviewState

    rendered = graph.get_graph()
    expected = {
        ('__start__', 'load_rubric'), ('load_rubric', 'segment'),
        ('segment', 'load_fixed_input'), ('segment', 'extract_claims'),
        ('extract_claims', 'write_record'), ('extract_claims', 'link_evidence'),
        ('link_evidence', 'write_record'), ('link_evidence', 'code_checks'),
        ('load_fixed_input', 'write_record'), ('load_fixed_input', 'code_checks'),
        ('code_checks', 'assess_dimension'), ('code_checks', 'aggregate'),
        ('assess_dimension', 'aggregate'), ('aggregate', 'verify_flags'),
        ('aggregate', 'write_record'), ('verify_flags', 'write_record'), ('write_record', '__end__'),
    }
    assert {(edge.source, edge.target) for edge in rendered.edges} == expected
    assert set(rendered.nodes) == {node for pair in expected for node in pair}
    assert 'llm' not in ReviewState.__annotations__ and 'settings' not in ReviewState.__annotations__


def test_code_only_rubric_uses_empty_dispatch_without_model_assessment(minimal_settings):
    for path in minimal_settings.rubrics_dir.glob('*.json'):
        rubric = json.loads(path.read_text())
        rubric['dimensions'][0]['checks'] = [{
            'check_id': 'arithmetic_mismatch', 'kind': 'code', 'scope': 'claim',
            'severity': 'high', 'description': 'Recompute sums.', 'detector': 'arithmetic_equation',
        }]
        path.write_text(json.dumps(rubric))
    stages = []

    def observe(stage, payload):
        stages.append(stage)
        return demo_response(stage, payload)

    record = review(ReviewRequest(report=REPORT, runs=3), minimal_settings, FakeLLM(observe))
    assert record.status == 'complete' and record.valid_runs == 3
    assert stages == ['extract', 'evidence'] and record.verification_status == 'not_run'
    assert len(record.flags) == 1 and record.flags[0].origin == 'code'


def test_fixed_mode_code_checks_cover_sentences_not_selected_as_claims(settings):
    fixed = FixedInput(claims=[], evidence=[])
    record = review(ReviewRequest(report=REPORT), settings, FakeLLM(), fixed)
    assert record.status == 'complete' and record.mode == 'fixed'
    flag, = record.flags
    assert flag.origin == 'code' and flag.claim_id is None and flag.target.target_id == 'p2.s1'


def test_payloads_use_inline_ids_without_character_offsets(settings):
    payloads = []

    def observe(stage, payload):
        payloads.append((stage, payload))
        return demo_response(stage, payload)

    record = review(ReviewRequest(report=REPORT), settings, FakeLLM(observe))
    assert record.status == 'complete'
    for _stage, payload in payloads:
        text = json.dumps(payload)
        assert '"start"' not in text and '"end"' not in text
        assert payload['report'].count('Training drove growth.') == 1
        assert '[p1.s1' in payload['report']
    for stage, payload in payloads:
        if stage in ('assess', 'assess_final'):
            assert_assessment_check_fields(payload)
            assert all(check['examples'] for check in payload['checks'])


def test_reusing_a_model_instance_fails_without_extra_calls(settings):
    llm = FakeLLM()
    first = review(ReviewRequest(report=REPORT), settings, llm)
    calls = llm.usage.calls
    assert first.status == 'complete'
    with pytest.raises(CharonError, match='^llm_already_used$'):
        review(ReviewRequest(report=REPORT), settings, llm)
    assert llm.usage.calls == calls


def test_assessment_sends_optional_standard_without_rule_metadata(minimal_settings):
    standard = 'Synthetic test standard: use a control comparison.'
    for path in minimal_settings.rubrics_dir.glob('*.json'):
        rubric = json.loads(path.read_text())
        rubric['dimensions'][0]['checks'][0]['standard'] = standard
        path.write_text(json.dumps(rubric))
    observed = []

    def inspect(stage, payload):
        if stage == 'assess':
            observed.extend(payload['checks'])
            assert_assessment_check_fields(payload)
        return demo_response(stage, payload)

    record = review(ReviewRequest(report=REPORT), minimal_settings, FakeLLM(inspect))
    assert record.status == 'complete' and len(observed) == 1
    assert observed[0]['standard'] == standard
