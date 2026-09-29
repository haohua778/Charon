"""Serializable contracts shared by the API, fixed DAG, and offline evaluation."""
from __future__ import annotations

from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

Scope = Literal['claim', 'section', 'document']
Severity = Literal['low', 'medium', 'high']
Variant = Literal['specific', 'vague']
Answer = StrictBool | StrictStr
REPORT_MAX_CHARACTERS = 30000


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Target(Contract):
    target_id: str
    scope: Scope
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode='after')
    def valid_span(self) -> Target:
        if self.end <= self.start:
            raise ValueError('Target must cover a nonempty source span')
        return self


class Unit(Contract):
    unit_id: str
    kind: Literal['sentence', 'table_row', 'heading']
    paragraph: int = Field(ge=1)
    section_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode='after')
    def valid_span(self) -> Unit:
        if self.end <= self.start:
            raise ValueError('Unit must cover a nonempty source span')
        return self


class Claim(Contract):
    claim_id: str
    target: Target
    text: str = Field(min_length=1)
    section_id: str
    unit_ids: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_identity(self) -> Claim:
        if self.claim_id != self.target.target_id or self.target.scope != 'claim':
            raise ValueError('Claim identity must match its claim target')
        return self


class ClaimRange(Contract):
    first: str
    last: str


class ExtractionOutput(Contract):
    claims: list[ClaimRange] = Field(max_length=80)


class EvidenceLink(Contract):
    claim_id: str
    relation: Literal['supports', 'contradicts', 'missing']
    unit_id: str | None = None
    quote: str | None = Field(default=None, min_length=1, max_length=8000)


class EvidenceOutput(Contract):
    evidence: list[EvidenceLink] = Field(max_length=160)


class FixedInput(Contract):
    claims: list[Claim] = Field(max_length=80)
    evidence: list[EvidenceLink] = Field(max_length=160)


class Question(Contract):
    field: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    type: Literal['bool', 'enum']
    ask: str = Field(min_length=1, max_length=4000)
    values: list[str] = Field(default_factory=list)
    quote_required_when: list[Answer] = Field(default_factory=list)

    def accepts(self, value: object) -> bool:
        return type(value) is bool if self.type == 'bool' else type(value) is str and value in self.values

    @model_validator(mode='after')
    def valid_values(self) -> Question:
        if self.type == 'bool' and self.values:
            raise ValueError('Boolean questions cannot have enum values')
        if self.type == 'enum' and (not self.values or len(set(self.values)) != len(self.values)):
            raise ValueError('Enum questions require distinct values')
        if any(not self.accepts(v) for v in self.quote_required_when):
            raise ValueError('Invalid quote condition value')
        return self


class Example(Contract):
    text: str
    flag: StrictBool
    why: str


class RubricCheck(Contract):
    check_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    kind: Literal['code', 'model_assessed', 'needs_standard']
    scope: Scope
    severity: Severity | None = None
    description: str = Field(min_length=1, max_length=4000)
    questions: list[Question] = Field(default_factory=list)
    anchor_field: str | None = None
    flag_when: dict[str, list[Answer]] = Field(default_factory=dict)
    reason_template: str | None = None
    examples: list[Example] = Field(default_factory=list)
    tools: list[Literal['calculator']] = Field(default_factory=list, max_length=1)
    detector: Literal['arithmetic_equation', 'percent_change', 'missing_reference'] | None = None
    pending_question: str | None = None
    standard: str | None = None

    @model_validator(mode='after')
    def valid_kind(self) -> RubricCheck:
        if self.kind != 'model_assessed':
            if (self.questions or self.flag_when or self.anchor_field is not None
                    or self.reason_template is not None or self.examples or self.tools or self.standard):
                raise ValueError('Only model assessed checks may define questions and rules')
            if self.kind == 'code':
                if self.detector is None or self.severity is None or self.scope != 'claim' or self.pending_question:
                    raise ValueError('Code checks require a detector, claim scope, and severity')
            elif self.severity is not None or self.detector is not None or not (self.pending_question or '').strip():
                raise ValueError('Standard checks require a pending question and no severity')
            return self
        if self.severity is None or self.detector is not None or self.pending_question is not None:
            raise ValueError('Invalid model assessed check metadata')
        questions = {q.field: q for q in self.questions}
        if not questions or len(questions) != len(self.questions):
            raise ValueError('Question fields must be nonempty and unique')
        if self.anchor_field not in questions:
            raise ValueError('Anchor field must name a question')
        if not self.flag_when or not self.reason_template:
            raise ValueError('Model assessed checks require a rule and reason template')
        for field, values in self.flag_when.items():
            if field not in questions or not values or any(not questions[field].accepts(v) for v in values):
                raise ValueError('Rule references an unknown field or invalid value')
        try:
            parts = list(Formatter().parse(self.reason_template))
        except ValueError as exc:
            raise ValueError('Invalid reason template') from exc
        for _, field, spec, conversion in parts:
            if field is not None and (field not in questions and field not in {f'{q}.quote' for q in questions}):
                raise ValueError('Reason template references an unknown field')
            if spec or conversion:
                raise ValueError('Reason template formatting and conversions are not supported')
        return self


class RubricDimension(Contract):
    dimension_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    name: str = Field(min_length=1)
    checks: list[RubricCheck] = Field(min_length=1)


class Rubric(Contract):
    rubric_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    analysis_type: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    version: str
    variant: Variant
    dimensions: list[RubricDimension] = Field(min_length=1)

    @property
    def checks(self) -> list[RubricCheck]:
        return [check for dimension in self.dimensions for check in dimension.checks]

    @model_validator(mode='after')
    def unique_checks(self) -> Rubric:
        dims = [d.dimension_id for d in self.dimensions]
        ids = [c.check_id for c in self.checks]
        if len(dims) != len(set(dims)) or len(ids) != len(set(ids)) or len(ids) > 20:
            raise ValueError('Rubric must have unique dimensions and at most 20 unique checks')
        return self


class UnitQuote(Contract):
    unit_id: str
    text: str = Field(min_length=1, max_length=8000)


class Assessment(Contract):
    check_id: str
    target_id: str
    answers: dict[str, Answer]
    quotes: dict[str, UnitQuote] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=2000)


class ToolRequest(Contract):
    check_id: str
    name: Literal['calculator']
    expression: str = Field(min_length=1, max_length=160)


class AssessmentOutput(Contract):
    assessments: list[Assessment] = Field(default_factory=list)
    tool_requests: list[ToolRequest] = Field(default_factory=list, max_length=2)


class VerificationRow(Contract):
    flag_id: str
    premise_supported: Literal['yes', 'partial', 'no']
    support: list[UnitQuote] = Field(default_factory=list)
    counter: list[UnitQuote] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)


class VerificationOutput(Contract):
    verifications: list[VerificationRow]


class Verification(Contract):
    label: Literal['supported', 'partially_supported', 'premise_not_supported', 'counter_evidence_found']
    support: list[UnitQuote] = Field(default_factory=list)
    counter: list[UnitQuote] = Field(default_factory=list)
    note: str | None = None


class Flag(Contract):
    check_id: str
    dimension: str
    origin: Literal['code', 'model']
    target: Target
    claim_id: str | None = None
    severity: Severity
    reason: str
    quote: str
    answers: dict[str, Answer] | None = None
    quotes: dict[str, UnitQuote] = Field(default_factory=dict)
    note: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.check_id, self.target.target_id


class AggregatedFlag(Flag):
    flag_id: str
    occurrences: int | None = Field(default=None, ge=1)
    valid_runs: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    also_reported_by_model: bool = False
    verification: Verification | None = None


class PendingStandard(Contract):
    check_id: str
    dimension: str
    question: str


class Usage(Contract):
    calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class RunResult(Contract):
    run_index: int = Field(ge=0)
    status: Literal['complete', 'failed']
    flags: list[Flag] = Field(default_factory=list)
    error_code: str | None = None
    elapsed_seconds: float = Field(ge=0)


class ReviewRequest(Contract):
    report: str = Field(min_length=1, max_length=REPORT_MAX_CHARACTERS)
    rubric_id: str = Field(default='program_evaluation', pattern=r'^[a-z][a-z0-9_]{0,63}$')
    variant: Variant = 'specific'
    runs: int = Field(default=1, ge=1, le=5)
    tools_enabled: bool = True
    verify_enabled: bool = True

    @field_validator('report')
    @classmethod
    def nonblank_report(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Report cannot be blank')
        return value


class ReviewRecord(Contract):
    review_id: str
    created_at: str
    status: Literal['complete', 'partial', 'failed']
    human_status: Literal['pending'] = 'pending'
    unchecked_check_ids: list[str] = Field(default_factory=list)
    pending_standards: list[PendingStandard] = Field(default_factory=list)
    decisions: list[dict[str, str]] = Field(default_factory=list)
    report: str
    rubric: Rubric
    analysis_type: str
    model: str
    provenance: Literal['offline_fake', 'live']
    mode: Literal['fixed', 'end_to_end']
    tools_enabled: bool
    verify_enabled: bool
    verification_status: Literal['not_run', 'complete', 'failed'] = 'not_run'
    verification_error_code: str | None = None
    requested_runs: int
    valid_runs: int
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceLink] = Field(default_factory=list)
    runs: list[RunResult] = Field(default_factory=list)
    flags: list[AggregatedFlag] = Field(default_factory=list)
    error_code: str | None = None
    usage: Usage
    elapsed_seconds: float = Field(ge=0)
