"""Pure validation and rubric evaluation: the model supplies answers, not verdicts."""
from __future__ import annotations

from string import Formatter

from app.core.errors import CharonError
from app.schemas import (
    AggregatedFlag, AssessmentOutput, Claim, Flag, RubricCheck, Target, Unit,
    UnitQuote, Verification, VerificationOutput,
)


def flag_when_matches(check: RubricCheck, answers: dict[str, bool | str]) -> bool:
    return all(answers.get(field) in allowed for field, allowed in check.flag_when.items())


def render_reason(template: str, answers: dict[str, bool | str],
                  quotes: dict[str, UnitQuote]) -> str:
    """Render only validated field and field.quote placeholders; never evaluate code."""
    pieces = []
    for literal, field, spec, conversion in Formatter().parse(template):
        pieces.append(literal)
        if field is None:
            continue
        if spec or conversion:
            raise CharonError('invalid_rubric')
        if field.endswith('.quote'):
            quote = quotes.get(field[:-6])
            pieces.append(quote.text if quote else '')
        elif field in answers:
            value = answers[field]
            pieces.append(str(value).lower() if isinstance(value, bool) else value)
        else:
            raise CharonError('invalid_rubric')
    return ''.join(pieces)


def validate_unit_quote(quote: UnitQuote, report: str, units: dict[str, Unit],
                        error_code: str) -> Unit:
    unit = units.get(quote.unit_id)
    if (unit is None or unit.kind not in ('sentence', 'table_row')
            or not quote.text.strip() or quote.text not in report[unit.start:unit.end]):
        raise CharonError(error_code)
    return unit


def validate_assessments(output: AssessmentOutput, report: str, units: list[Unit],
                         targets: list[Target], checks: list[RubricCheck]) -> None:
    checks_by_id = {check.check_id: check for check in checks if check.kind == 'model_assessed'}
    targets_by_id = {target.target_id: target for target in targets}
    expected = {(check.check_id, target.target_id) for check in checks_by_id.values()
                for target in targets if target.scope == check.scope}
    actual = [(row.check_id, row.target_id) for row in output.assessments]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise CharonError('invalid_assessment_coverage')
    units_by_id = {unit.unit_id: unit for unit in units}
    for row in output.assessments:
        check, target = checks_by_id[row.check_id], targets_by_id[row.target_id]
        questions = {question.field: question for question in check.questions}
        if set(row.answers) != set(questions) or not set(row.quotes) <= set(questions):
            raise CharonError('invalid_assessment_answer')
        for field, question in questions.items():
            answer = row.answers[field]
            if question.type == 'bool':
                legal = type(answer) is bool
            else:
                legal = type(answer) is str and answer in question.values
            if not legal:
                raise CharonError('invalid_assessment_answer')
            if answer in question.quote_required_when and field not in row.quotes:
                raise CharonError('invalid_assessment_quote')
        for field, quote in row.quotes.items():
            unit = validate_unit_quote(quote, report, units_by_id, 'invalid_assessment_quote')
            if field == check.anchor_field and not (target.start <= unit.start and unit.end <= target.end):
                raise CharonError('invalid_anchor_quote')
        if (flag_when_matches(check, row.answers) and target.scope == 'claim'
                and check.anchor_field not in row.quotes):
            raise CharonError('invalid_anchor_quote')


def flags_from_assessments(output: AssessmentOutput, report: str, units: list[Unit],
                           targets: list[Target], checks: list[RubricCheck],
                           dimension: str, claims: list[Claim]) -> list[Flag]:
    validate_assessments(output, report, units, targets, checks)
    checks_by_id = {check.check_id: check for check in checks}
    targets_by_id = {target.target_id: target for target in targets}
    claim_ids = {claim.claim_id for claim in claims}
    flags = []
    for row in output.assessments:
        check = checks_by_id[row.check_id]
        if not flag_when_matches(check, row.answers):
            continue
        target = targets_by_id[row.target_id]
        anchor = row.quotes.get(check.anchor_field)
        flags.append(Flag(check_id=check.check_id, dimension=dimension, origin='model',
                          target=target, claim_id=target.target_id if target.target_id in claim_ids else None,
                          severity=check.severity,
                          reason=render_reason(check.reason_template, row.answers, row.quotes),
                          quote=anchor.text if anchor else '', answers=row.answers,
                          quotes=row.quotes, note=row.note))
    return flags


def validate_verifications(output: VerificationOutput, report: str, units: list[Unit],
                           flags: list[AggregatedFlag]) -> None:
    expected = {flag.flag_id for flag in flags}
    actual = [row.flag_id for row in output.verifications]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise CharonError('invalid_verification_coverage')
    units_by_id = {unit.unit_id: unit for unit in units}
    for row in output.verifications:
        for quote in [*row.support, *row.counter]:
            validate_unit_quote(quote, report, units_by_id, 'invalid_verification_quote')


def verification_results(output: VerificationOutput) -> dict[str, Verification]:
    result = {}
    for row in output.verifications:
        if row.counter:
            label = 'counter_evidence_found'
        elif row.premise_supported == 'no':
            label = 'premise_not_supported'
        elif row.premise_supported == 'partial':
            label = 'partially_supported'
        else:
            label = 'supported'
        result[row.flag_id] = Verification(label=label, support=row.support,
                                          counter=row.counter, note=row.note)
    return result
