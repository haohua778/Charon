"""Deterministic English Markdown units; all spans use original Unicode characters."""
from __future__ import annotations

import re

from app.core.errors import CharonError
from app.schemas import REPORT_MAX_CHARACTERS, Claim, Target, Unit

ABBREVIATIONS = frozenset(word.lower() for word in (
    'e.g.', 'i.e.', 'etc.', 'vs.', 'Mr.', 'Mrs.', 'Ms.', 'Dr.', 'Inc.', 'Ltd.',
    'Co.', 'Corp.', 'No.', 'Fig.', 'approx.', 'U.S.', 'U.K.', 'Jan.', 'Feb.',
    'Mar.', 'Apr.', 'Jun.', 'Jul.', 'Aug.', 'Sep.', 'Sept.', 'Oct.', 'Nov.', 'Dec.',
))
HEADING = re.compile(r'^#{1,6}[ \t]+')
LIST_ITEM = re.compile(r'^[ \t]*(?:[-*+] |\d+[.)][ \t]+)')
TABLE_SEPARATOR = re.compile(r'^\|[\s:|\-]+\|?\s*$')
CLAIM_ID = re.compile(r'^p([1-9]\d*)\.s([1-9]\d*)(?:-s([1-9]\d*))?$')


def _trim(report: str, start: int, end: int) -> tuple[int, int]:
    while start < end and report[start].isspace():
        start += 1
    while end > start and report[end - 1].isspace():
        end -= 1
    return start, end


def _sentences(report: str, start: int, end: int) -> list[tuple[int, int]]:
    text = report[start:end]
    marker = LIST_ITEM.match(text)
    content_start = marker.end() if marker else 0
    spans = []
    previous = start
    for match in re.finditer(r'[.!?]["\x27)\]]*', text):
        i = match.start()
        if i < content_start:
            continue
        mark = text[i]
        if mark == '.':
            if (i > 0 and text[i - 1] == '.') or (i + 1 < len(text) and text[i + 1] == '.'):
                continue
            if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
                continue
            token = re.search(r'[A-Za-z]+(?:\.[A-Za-z]+)*\.$', text[:i + 1])
            if token and (token.group().lower() in ABBREVIATIONS or re.fullmatch(r'[A-Z]\.', token.group())):
                continue
        tail = text[match.end():]
        if tail.strip() and not re.match(r'\s+[A-Z0-9"\x27(\[]', tail):
            continue
        cut = start + match.end()
        left, right = _trim(report, previous, cut)
        if left < right:
            spans.append((left, right))
        previous = cut
    left, right = _trim(report, previous, end)
    if left < right:
        spans.append((left, right))
    return spans


def segment_report(report: str) -> tuple[list[Unit], list[Target]]:
    if not report.strip() or len(report) > REPORT_MAX_CHARACTERS:
        raise ValueError(f'Report must contain 1 to {REPORT_MAX_CHARACTERS} characters')
    # Blank lines terminate blocks; a heading always forms its own block.
    blocks: list[list[tuple[int, int]]] = []
    pending: list[tuple[int, int]] = []
    cursor = 0
    for line in report.splitlines(keepends=True):
        start, end = cursor, cursor + len(line)
        cursor = end
        if not line.strip():
            if pending:
                blocks.append(pending)
                pending = []
        elif HEADING.match(line):
            if pending:
                blocks.append(pending)
                pending = []
            blocks.append([(start, end)])
        else:
            pending.append((start, end))
    if pending:
        blocks.append(pending)

    units: list[Unit] = []
    heading_starts: list[tuple[str, int]] = []
    section_id = 'sec0'
    for paragraph, lines in enumerate(blocks, 1):
        first, last = lines[0][0], lines[-1][1]
        if HEADING.match(report[first:last]):
            section_id = f'sec{len(heading_starts) + 1}'
            heading_starts.append((section_id, first))
            left, right = _trim(report, first, last)
            units.append(Unit(unit_id=f'p{paragraph}', kind='heading', paragraph=paragraph,
                              section_id=section_id, start=left, end=right))
            continue
        spans: list[tuple[str, int, int]] = []
        prose: list[tuple[int, int]] = []

        def flush_prose() -> None:
            if prose:
                spans.extend(('sentence', left, right) for left, right in
                             _sentences(report, prose[0][0], prose[-1][1]))
                prose.clear()

        for start, end in lines:
            line = report[start:end]
            if line.startswith('|'):
                flush_prose()
                if not TABLE_SEPARATOR.fullmatch(line.strip()):
                    left, right = _trim(report, start, end)
                    spans.append(('table_row', left, right))
            elif LIST_ITEM.match(line):
                flush_prose()
                spans.extend(('sentence', left, right) for left, right in _sentences(report, start, end))
            else:
                prose.append((start, end))
        flush_prose()
        for number, (kind, start, end) in enumerate(spans, 1):
            units.append(Unit(unit_id=f'p{paragraph}.s{number}', kind=kind, paragraph=paragraph,
                              section_id=section_id, start=start, end=end))

    targets = [Target(target_id='doc', scope='document', start=0, end=len(report))]
    sections = list(heading_starts)
    first_heading = heading_starts[0][1] if heading_starts else len(report)
    if report[:first_heading].strip():
        sections.insert(0, ('sec0', 0))
    for index, (name, start) in enumerate(sections):
        end = sections[index + 1][1] if index + 1 < len(sections) else len(report)
        left, right = _trim(report, start, end)
        if left < right:
            targets.append(Target(target_id=name, scope='section', start=left, end=right))
    targets.extend(Target(target_id=u.unit_id, scope='claim', start=u.start, end=u.end)
                   for u in units if u.kind == 'sentence')
    return units, targets


def source_targets(report: str) -> list[Target]:
    return segment_report(report)[1]


def claim_from_range(report: str, units: list[Unit], first: str, last: str) -> Claim:
    by_id = {unit.unit_id: i for i, unit in enumerate(units)}
    if first not in by_id or last not in by_id or by_id[first] > by_id[last]:
        raise CharonError('invalid_claim_target')
    selected = units[by_id[first]:by_id[last] + 1]
    if any(u.kind != 'sentence' or u.paragraph != selected[0].paragraph for u in selected):
        raise CharonError('invalid_claim_target')
    target_id = first if first == last else f'{first}-s{last.rsplit(".s", 1)[1]}'
    start, end = selected[0].start, selected[-1].end
    return Claim(claim_id=target_id, target=Target(target_id=target_id, scope='claim', start=start, end=end),
                 text=report[start:end], section_id=selected[0].section_id, unit_ids=[u.unit_id for u in selected])


def resolve_target(report: str, target_id: str, units: list[Unit] | None = None) -> Target:
    generated_units, targets = segment_report(report)
    units = generated_units if units is None else units
    for target in targets:
        if target.target_id == target_id:
            return target
    match = CLAIM_ID.fullmatch(target_id)
    if not match:
        raise CharonError('invalid_claim_target')
    paragraph, first, last = match.groups()
    claim = claim_from_range(report, units, f'p{paragraph}.s{first}', f'p{paragraph}.s{last or first}')
    if claim.claim_id != target_id:
        raise CharonError('invalid_claim_target')
    return claim.target


def render_annotated(report: str, units: list[Unit]) -> str:
    from app.tools.cues import cue_tags

    markers: dict[int, str] = {}
    first_heading = next((u.start for u in units if u.kind == 'heading'), len(report))
    if report[:first_heading].strip():
        offset = len(report) - len(report.lstrip())
        markers[offset] = '[sec0] '
    for unit in units:
        if unit.kind == 'heading':
            marker = unit.section_id
        else:
            tags = cue_tags(report[unit.start:unit.end])
            marker = unit.unit_id + (f' | {", ".join(tags)}' if tags else '')
        markers[unit.start] = markers.get(unit.start, '') + f'[{marker}] '
    chunks: list[str] = []
    previous = 0
    for offset, marker in sorted(markers.items()):
        chunks.extend((report[previous:offset], marker))
        previous = offset
    chunks.append(report[previous:])
    return ''.join(chunks)
