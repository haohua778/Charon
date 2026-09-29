"""First-stage source checks with deterministic arithmetic and exact quotations."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.schemas import Claim, Flag, Rubric, Target, Unit

NUMBER = r'[-+]?(?:[$€£]\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?'
EQUATION = re.compile(rf'(?<![\w.,])({NUMBER}(?:\s*\+\s*{NUMBER})+)\s*=\s*({NUMBER})(?!\w|[.,]\d)')
FROM_TO = re.compile(rf'\bfrom\s+({NUMBER})\s+to\s+({NUMBER})(?!\w|[.,]\d)', re.I)
PERCENT = re.compile(r'(?<![\w.,])([-+]?\d+(?:\.\d+)?)\s*%')
UP = re.compile(r'\b(?:increase[ds]?|increasing|grew|grow[ns]?|rose|risen|rise[sn]?)\b', re.I)
DOWN = re.compile(r'\b(?:decrease[ds]?|decreasing|fell|fall[sn]?|declined?|declining)\b', re.I)
REFERENCE_END = r'(?!\w|[.,/–-][A-Za-z0-9])'
REFERENCE = re.compile(r'\b(?:Table\s+\d+|Figure\s+\d+|Exhibit\s+\d+|'
                       r'Appendix\s+(?-i:[A-Z]|[IVXLCDM]+|\d+))'
                       + REFERENCE_END, re.I)
ARITHMETIC_OPERATORS = '+-−*/×÷=^'
UNSUPPORTED_MAGNITUDE = re.compile(r'^(?:[KMBT]|bn|thousand|million|billion)\b', re.I)


def _number(value: str) -> Decimal:
    return Decimal(re.sub(r'[$€£,%\s]', '', value))


def _right_continues_expression(text: str, end: int, *, percent_annotation: bool = False) -> bool:
    tail = text[end:].lstrip()
    # Closing punctuation may wrap an equation that belongs to a larger expression.
    tail = re.sub(r'^[)\]\x27"]+\s*', '', tail)
    if tail.startswith('('):
        parenthesis = re.match(r'\(([^()]*)\)', tail)
        if parenthesis is None:
            return True
        content = parenthesis.group(1).strip()
        explanatory = bool(re.search(r'[A-Za-z]{2,}', content)) and not UNSUPPORTED_MAGNITUDE.fullmatch(content)
        percentage = percent_annotation and PERCENT.fullmatch(content)
        if not explanatory and not percentage:
            return True
        tail = tail[parenthesis.end():].lstrip()
    if percent_annotation and re.match(r'^-\s+(?:a|an|the)\s+', tail, re.I):
        return False
    return bool(tail and (tail[0] in ARITHMETIC_OPERATORS + '('
                         or UNSUPPORTED_MAGNITUDE.match(tail)))


def _partial_equation(text: str, start: int, end: int) -> bool:
    prefix = text[:start]
    # Ignore matching Markdown emphasis around this whole equation, not lone
    # multiplication operators. The returned quote still uses original offsets.
    for marker in ('***', '**', '*'):
        if prefix.endswith(marker) and text[end:].startswith(marker):
            after = text[end + len(marker):]
            if not prefix[:-len(marker)].endswith('*') and not re.match(r'[\w*]', after):
                prefix = prefix[:-len(marker)]
                end += len(marker)
                break
    list_marker = re.fullmatch(r'[ \t]*(?:[-*+]|\d+[.)])[ \t]+', prefix)
    prefix = prefix.rstrip()
    signed_tail = text[start] in '+-' and prefix and (prefix[-1].isdigit() or prefix[-1] in ')]')
    parenthesized = re.search(r'\([\d\s.,()+\-−*/×÷=^$€£%]+\)$', prefix)
    numeric_parenthesis = parenthesized is not None and any(char.isdigit() for char in parenthesized.group())
    if not list_marker and prefix and (prefix[-1] in ARITHMETIC_OPERATORS + '$€£¥₹'
                                       or signed_tail or numeric_parenthesis):
        return True
    return _right_continues_expression(text, end)


def _mixed_currencies(text: str) -> bool:
    return len(set(re.findall(r'[$€£]', text))) > 1


def _display_decimal(value: Decimal) -> str:
    text = format(value, '.4f').rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


def arithmetic_equation(text: str) -> list[tuple[str, str]]:
    problems = []
    for match in EQUATION.finditer(text):
        if _partial_equation(text, match.start(), match.end()) or _mixed_currencies(match.group(0)):
            continue
        # Currency and percentage annotations do not affect addition in stated units.
        terms = re.findall(NUMBER, match.group(1))
        percentage_marks = ['%' in value for value in [*terms, match.group(2)]]
        if any(percentage_marks) and not all(percentage_marks):
            continue
        try:
            actual, stated = sum((_number(term) for term in terms), Decimal(0)), _number(match.group(2))
        except InvalidOperation:
            continue
        difference = abs(actual - stated)
        last_unit = Decimal(1).scaleb(stated.as_tuple().exponent)
        if difference > abs(stated) * Decimal('0.005') or difference > last_unit / 2:
            problems.append((match.group(0), f'The stated sum is {stated}; recalculation gives {actual}.'))
    return problems


def percent_change(text: str) -> list[tuple[str, str]]:
    changes, percentages = list(FROM_TO.finditer(text)), list(PERCENT.finditer(text))
    up, down = bool(UP.search(text)), bool(DOWN.search(text))
    if len(changes) != 1 or len(percentages) != 1 or up == down:
        return []
    match, percent = changes[0], percentages[0]
    if _mixed_currencies(match.group(0)) or _right_continues_expression(text, match.end(), percent_annotation=True):
        return []
    # Do not interpret percentages in the endpoints as a claimed growth rate.
    if match.start() <= percent.start() < match.end():
        return []
    start, end = _number(match.group(1)), _number(match.group(2))
    if start == 0:
        return []
    actual = (end - start) / start * 100
    raw = Decimal(percent.group(1))
    claimed = raw if up else -abs(raw)
    wrong_direction = (up and actual < 0) or (down and actual > 0)
    if abs(actual - claimed) > Decimal('0.5') or wrong_direction:
        left, right = min(match.start(), percent.start()), max(match.end(), percent.end())
        return [(text[left:right], f'The stated change is {_display_decimal(claimed)}%; '
                 f'recalculation gives {_display_decimal(actual)}%.')]
    return []


def missing_reference(text: str, report: str) -> list[tuple[str, str]]:
    problems = []
    for match in REFERENCE.finditer(text):
        label = match.group(0)
        kind, identifier = label.split()
        prefix = re.compile(r'(?i:' + re.escape(kind) + r')[ \t]+' + re.escape(identifier) + REFERENCE_END)
        found = False
        for line in report.splitlines():
            line = line.lstrip(' \t')
            heading = re.match(r'^#{1,6}[ \t]+', line)
            if heading:
                line = line[heading.end():]
            # Both headings and captions may use Markdown emphasis around the label.
            line = re.sub(r'^(?:\*\*|__)', '', line)
            start = prefix.match(line)
            if start is None:
                continue
            tail = re.sub(r'^(?:\*\*|__)', '', line[start.end():]).strip()
            if heading or not tail or re.match(r'^(?::|—|\.(?:[ \t]|$))', tail):
                found = True
                break
        if not found:
            problems.append((label, f'The report refers to {label}, but no matching heading or caption is present.'))
    return problems


def run_detectors(report: str, units: list[Unit], claims: list[Claim],
                  rubric: Rubric) -> list[Flag]:
    """Run each code check once over every sentence, independent of extraction."""
    flags = []
    for dimension in rubric.dimensions:
        for check in dimension.checks:
            if check.kind != 'code':
                continue
            for unit in units:
                if unit.kind != 'sentence':
                    continue
                text = report[unit.start:unit.end]
                if check.detector == 'arithmetic_equation':
                    problems = arithmetic_equation(text)
                elif check.detector == 'percent_change':
                    problems = percent_change(text)
                else:
                    problems = missing_reference(text, report)
                if not problems:
                    continue
                # The fixed per-run key permits one finding per check and sentence.
                quote, reason = problems[0]
                claim_id = next((claim.claim_id for claim in claims if unit.unit_id in claim.unit_ids), None)
                flags.append(Flag(check_id=check.check_id, dimension=dimension.dimension_id,
                                  origin='code', target=Target(target_id=unit.unit_id, scope='claim',
                                                              start=unit.start, end=unit.end),
                                  claim_id=claim_id, severity=check.severity, reason=reason, quote=quote))
    return flags
