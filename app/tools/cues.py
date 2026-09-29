"""Deterministic hints inside one report. Hints never filter model input."""
from __future__ import annotations

import re

from app.schemas import Claim, Target, Unit

CAUSAL_TERMS = ('because', 'due to', 'driven by', 'caused', 'led to', 'resulted in',
                'as a result', 'thanks to', 'attributable')
FORECAST_TERMS = ('will', 'expect', 'forecast', 'project', 'anticipate', 'guarantee',
                  'ensure', 'certain')
COMPARISON_TERMS = ('higher', 'lower', 'than', 'compared', 'versus', 'vs.',
                    'outperform', 'increase', 'decrease')
NUMBER_PATTERN = re.compile(r'(?<![\w.,])(?:[$€£]\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?!\w|[.,]\d)')
PERIOD_PATTERN = re.compile(r'\b(?:Q[1-4]|(?:19|20)\d{2})\b', re.IGNORECASE)
NAME_PATTERN = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b')


def cue_tags(text: str) -> list[str]:
    """Return labels in a stable order; all original sentences remain visible."""
    def contains(terms: tuple[str, ...]) -> bool:
        return any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I)
                   for term in terms)

    result = []
    if contains(CAUSAL_TERMS):
        result.append('causal')
    if re.search(r'\d|[%$€£]', text):
        result.append('quant')
    if contains(FORECAST_TERMS):
        result.append('forecast')
    if contains(COMPARISON_TERMS):
        result.append('comparison')
    return result


def _shared_terms(text: str) -> set[str]:
    return {match.group(0) for pattern in (NUMBER_PATTERN, PERIOD_PATTERN, NAME_PATTERN)
            for match in pattern.finditer(text)}


def candidate_units(report: str, units: list[Unit], target: Target,
                    limit: int = 5) -> list[str]:
    """Rank evidence outside a target by shared literals, then paragraph distance."""
    terms = _shared_terms(report[target.start:target.end])
    target_paragraphs = [unit.paragraph for unit in units
                         if unit.start < target.end and target.start < unit.end]
    if not terms or not target_paragraphs:
        return []
    scored = []
    for unit in units:
        if unit.kind not in ('sentence', 'table_row'):
            continue
        if unit.start < target.end and target.start < unit.end:
            continue
        shared = len(terms & _shared_terms(report[unit.start:unit.end]))
        if shared:
            distance = min(abs(unit.paragraph - paragraph) for paragraph in target_paragraphs)
            scored.append((-shared, distance, unit.start, unit.unit_id))
    return [item[3] for item in sorted(scored)[:max(0, min(limit, 5))]]


def evidence_candidates(report: str, units: list[Unit], claim: Claim,
                        limit: int = 5) -> list[str]:
    return candidate_units(report, units, claim.target, limit)
