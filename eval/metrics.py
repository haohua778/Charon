"""Source-overlap metrics with explicit human and completeness gates."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import combinations
from typing import Any, Literal

from pydantic import Field, model_validator

from app.matching import match_items, position_key
from app.schemas import AggregatedFlag, Contract, Flag, RunResult

Key = tuple[str, str]


class AnswerIssue(Contract):
    check_id: str
    target_id: str = Field(pattern=r'^(?:doc|sec\d+|p[1-9]\d*\.s[1-9]\d*(?:-s[1-9]\d*)?)$')
    description: str = Field(min_length=1)
    excerpt: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def require_excerpt(self) -> AnswerIssue:
        if self.target_id != 'doc' and self.excerpt is None:
            raise ValueError('Claim and section answer issues require an exact source excerpt')
        return self

    @property
    def key(self) -> Key:
        return self.check_id, self.target_id


class AnswerKey(Contract):
    status: Literal['draft', 'approved'] = 'draft'
    approval_note: str | None = None
    issues: list[AnswerIssue] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_issues(self) -> AnswerKey:
        keys = [issue.key for issue in self.issues]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate answer key (check_id, target_id)')
        if self.status == 'approved' and not (self.approval_note or '').strip():
            raise ValueError('An approved answer key requires a human approval note')
        return self


class Adjudication(Contract):
    check_id: str
    target_id: str
    status: Literal['pending', 'confirmed_error', 'confirmed_valid'] = 'pending'
    note: str | None = None

    @property
    def key(self) -> Key:
        return self.check_id, self.target_id


def validate_adjudications(
    answers: AnswerKey | None, adjudications: Sequence[Adjudication],
) -> dict[Key, Adjudication]:
    decisions = {decision.key: decision for decision in adjudications}
    if len(decisions) != len(adjudications):
        raise ValueError('Duplicate adjudication key (check_id, target_id)')
    erroneous_flags = [item for item in adjudications if item.status == 'confirmed_error']
    if answers and answers.status == 'approved' and match_items(answers.issues, erroneous_flags):
        raise ValueError('Approved answer issue contradicts an erroneous-flag adjudication')
    return decisions


def _unique(flags: Sequence[Flag]) -> list[Flag]:
    return sorted({flag.key: flag for flag in flags}.values(), key=position_key)


def compute_metrics(
    runs: Sequence[RunResult], *, requested_runs: int,
    answers: AnswerKey | None = None,
    adjudications: Sequence[Adjudication] = (),
    code_flags: Sequence[Flag] = (),
    aggregated_flags: Sequence[AggregatedFlag] | None = None,
    preparation_failed_runs: int = 0,
) -> dict[str, Any]:
    """Read actual runs; count preparation failures without fabricated runs.

    Separate end-to-end records can each contain run_index=0. Their list entries
    represent separate runs. Code findings are included once in recall and human
    adjudication, while consistency includes only complete model runs.
    """
    if (requested_runs < 1 or preparation_failed_runs < 0
            or len(runs) + preparation_failed_runs > requested_runs):
        raise ValueError('Invalid requested run count')
    decisions = validate_adjudications(answers, adjudications)
    complete_runs = [run for run in runs if run.status == 'complete']
    model_runs = [_unique([flag for flag in run.flags if flag.origin == 'model'])
                  for run in complete_runs]
    complete_batch = len(complete_runs) == requested_runs and not preparation_failed_runs
    occurrences: Counter[Key] = Counter(flag.key for flags in model_runs for flag in flags)
    models = _unique([flag for flags in model_runs for flag in flags])
    codes = _unique([*code_flags, *[flag for run in complete_runs for flag in run.flags if flag.origin == 'code']])
    code_model_matches = match_items(codes, models)
    replaced_models = {model_index for _, model_index in code_model_matches}
    flags = sorted([*codes, *[flag for index, flag in enumerate(models) if index not in replaced_models]], key=position_key)
    issues = answers.issues if answers else []
    answer_matches = match_items(flags, issues)
    matched_flags = {left for left, _ in answer_matches}
    decision_counts: Counter[str] = Counter(decisions[flag.key].status if flag.key in decisions else 'pending' for flag in flags)
    labels = ('supported', 'partially_supported', 'premise_not_supported', 'counter_evidence_found', 'unverified')
    distribution = {label: {'confirmed_error': 0, 'confirmed_valid': 0, 'pending': 0} for label in labels}
    # Independent e2e reviews can produce different verification labels for the
    # same target. Preserve every review observation instead of picking one.
    observations = list(aggregated_flags) if aggregated_flags is not None else flags
    for flag in observations:
        verification = getattr(flag, 'verification', None)
        label = verification.label if verification else 'unverified'
        decision = decisions[flag.key].status if flag.key in decisions else 'pending'
        distribution[label][decision] += 1
    pair_scores: list[float] = []
    double_empty_pairs = 0
    if complete_batch:
        for left, right in combinations(model_runs, 2):
            if not left and not right:
                double_empty_pairs += 1
                continue
            matched = len(match_items(left, right))
            pair_scores.append(matched / (len(left) + len(right) - matched))
    approved = answers is not None and answers.status == 'approved'
    recall_available = complete_batch and approved and bool(issues)
    pending = decision_counts['pending']
    return {
        'complete_batch': complete_batch,
        'requested_runs': requested_runs,
        'valid_runs': len(complete_runs),
        'failed_runs': len(runs) - len(complete_runs) + preparation_failed_runs,
        'missing_runs': requested_runs - len(runs) - preparation_failed_runs,
        'detected_empty_runs': sum(not flags and not codes for flags in model_runs),
        'unique_flag_count': len(flags),
        'answer_key_status': answers.status if answers else 'absent',
        'flags': [
            {
                'check_id': flag.check_id, 'target_id': flag.target.target_id,
                'origin': flag.origin,
                'occurrences': occurrences[flag.key] if flag.origin == 'model' else None,
                'valid_runs': len(complete_runs),
                'confidence': occurrences[flag.key] / len(complete_runs)
                if complete_batch and flag.origin == 'model' else None,
                'adjudication': decisions[flag.key].status if flag.key in decisions else 'pending',
                'matches_answer_key': index in matched_flags,
            }
            for index, flag in enumerate(flags)
        ],
        'consistency': {
            'pairwise_jaccard': sum(pair_scores) / len(pair_scores) if pair_scores else None,
            'comparable_pairs': len(pair_scores),
            'double_empty_pairs': double_empty_pairs,
            'unavailable_reason': ('incomplete_batch' if not complete_batch else
                                   'fewer_than_two_runs' if len(model_runs) < 2 else
                                   'all_pairs_double_empty' if not pair_scores else None),
        },
        'error_flag_proportion': {
            'value': decision_counts['confirmed_error'] / len(flags)
            if complete_batch and flags and pending == 0 else None,
            'confirmed_error': decision_counts['confirmed_error'],
            'confirmed_valid': decision_counts['confirmed_valid'],
            'pending': pending,
            'denominator_all_unique_flags': len(flags),
            'pending_share': pending / len(flags) if flags else None,
            'unavailable_reason': ('incomplete_batch' if not complete_batch else
                                   'no_flags' if not flags else
                                   'pending_adjudication' if pending else None),
        },
        'recall': {
            'value': len(answer_matches) / len(issues) if recall_available else None,
            'matched_issue_count': len(answer_matches) if complete_batch and approved else None,
            'approved_issue_count': len(issues) if approved else None,
            'unavailable_reason': ('incomplete_batch' if not complete_batch else
                                   'answer_key_not_approved' if not approved else
                                   'empty_answer_key' if not issues else None),
        },
        'unmatched_flags': [
            {'check_id': flag.check_id, 'target_id': flag.target.target_id,
             'adjudication': decisions[flag.key].status if flag.key in decisions else 'pending'}
            for index, flag in enumerate(flags) if index not in matched_flags
        ],
        'verification_distribution': distribution,
        'verification_distribution_scope': 'review_flag_observations' if aggregated_flags is not None else 'unique_flags',
        'verification_observation_count': len(observations),
    }
