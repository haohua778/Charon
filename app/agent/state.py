"""Serializable graph state; model clients and settings belong to runtime context."""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, TypedDict

from app.core.config import Settings
from app.llm.client import LLM
from app.schemas import (
    AggregatedFlag, Claim, EvidenceLink, FixedInput, Flag, PendingStandard,
    ReviewRecord, ReviewRequest, Rubric, RunResult, Target, Unit,
)


@dataclass
class ReviewContext:
    llm: LLM
    settings: Settings


class ReviewState(TypedDict):
    request: ReviewRequest
    fixed_input: FixedInput | None
    review_id: str
    started_at: float
    rubric: NotRequired[Rubric]
    units: NotRequired[list[Unit]]
    targets: NotRequired[list[Target]]
    claims: NotRequired[list[Claim]]
    evidence: NotRequired[list[EvidenceLink]]
    preparation_error: NotRequired[str | None]
    code_flags: NotRequired[list[Flag]]
    pending_standards: NotRequired[list[PendingStandard]]
    dimension_results: Annotated[list[dict[str, Any]], operator.add]
    dimension_id: NotRequired[str]
    run_index: NotRequired[int]
    runs: NotRequired[list[RunResult]]
    flags: NotRequired[list[AggregatedFlag]]
    verification_status: NotRequired[str]
    verification_error_code: NotRequired[str | None]
    record: NotRequired[ReviewRecord]


class VerificationState(TypedDict):
    request: ReviewRequest
    rubric: Rubric
    units: list[Unit]
    flags: list[AggregatedFlag]
    dimension_id: NotRequired[str]
    verification_batches: Annotated[list[dict[str, Any]], operator.add]
    verification_status: NotRequired[str]
    verification_error_code: NotRequired[str | None]
