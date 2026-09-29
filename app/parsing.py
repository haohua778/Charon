"""Turn uploaded documents into the English Markdown that the segmenter expects.

Text files are decoded directly. PDFs, Word files and page images go through
an external MinerU command (by default ``scripts/mineru_text_parse.py`` run by
the interpreter of the MinerU checkout's environment) in a temporary
directory, so torch and the layout models never enter this project's lock file.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import shutil
import tempfile
from asyncio.subprocess import PIPE
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from app.core.config import Settings
from app.core.errors import CharonError
from app.schemas import REPORT_MAX_CHARACTERS, Contract

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = frozenset({'.md', '.markdown', '.txt'})
MINERU_SUFFIXES = frozenset({'.pdf', '.docx', '.png', '.jpg', '.jpeg'})
TABLE_TAG = re.compile(r'<(/?)table\b[^>]*>', re.I)
HTML_WRAPPER = re.compile(r'</?(?:html|body)\b[^>]*>', re.I)
# Inline markup MinerU emits inside prose; the text between the tags is kept.
INLINE_TAG = re.compile(r'</?(?:sup|sub|a|strong|em|b|i|s|u|span|br|del|ins|code)\b[^>]*>', re.I)
IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')


class ParsedDocument(Contract):
    filename: str
    parser: Literal['text', 'mineru']
    markdown: str
    characters: int
    fits_review_limit: bool


class _TableRows(HTMLParser):
    """Collect cell text row by row; nested tables and spans are flattened."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'table':
            self._depth += 1
        elif self._depth > 1 or tag in ('br', 'p'):
            if self._cell is not None:
                self._cell.append(' ')
        elif tag == 'tr':
            self.rows.append([])
        elif tag in ('td', 'th'):
            if not self.rows:
                self.rows.append([])
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == 'table':
            self._depth -= 1
        elif self._depth == 1 and tag in ('td', 'th') and self._cell is not None:
            text = ' '.join(''.join(self._cell).split()).replace('|', '\\|')
            self.rows[-1].append(text)
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _pipe_table(html: str) -> str:
    parser = _TableRows()
    parser.feed(html)
    rows = [row for row in parser.rows if any(cell for cell in row)]
    if not rows:
        return ''
    width = max(len(row) for row in rows)
    lines = ['| ' + ' | '.join(row + [''] * (width - len(row))) + ' |' for row in rows]
    lines.insert(1, '|' + ' --- |' * width)
    return '\n\n' + '\n'.join(lines) + '\n\n'


def _replace_tables(text: str) -> str:
    """Rewrite each outermost <table>; nested tables are flattened into the cell text."""
    pieces, depth, previous, start = [], 0, 0, 0
    for tag in TABLE_TAG.finditer(text):
        closing = bool(tag.group(1))
        if not closing and depth == 0:
            start = tag.start()
        depth = max(0, depth - 1) if closing else depth + 1
        if closing and depth == 0:
            pieces.extend((text[previous:start], _pipe_table(text[start:tag.end()])))
            previous = tag.end()
    pieces.append(text[previous:])
    return ''.join(pieces)


def normalize_markdown(text: str) -> str:
    """Rewrite parser output into the subset of Markdown the segmenter understands."""
    text = text.lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n')
    text = HTML_WRAPPER.sub('', text)
    text = _replace_tables(text)
    text = IMAGE.sub('', text)
    text = LINK.sub(r'\1', text)
    text = INLINE_TAG.sub('', text)
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + '\n' if text.strip() else ''


def mineru_command(settings: Settings) -> list[str]:
    """Resolve the configured command; a missing executable or script is a safe error."""
    parts = shlex.split(settings.mineru_command)
    executable = shutil.which(parts[0]) if parts else None
    if executable is None or not all(Path(part).is_file() for part in parts[1:] if part.endswith('.py')):
        raise CharonError('parser_unavailable')
    return [executable, *parts[1:]]


async def _run_mineru(data: bytes, suffix: str, settings: Settings) -> str:
    command = mineru_command(settings)
    with tempfile.TemporaryDirectory(prefix='charon-parse-') as workspace:
        source = Path(workspace) / 'input' / f'document{suffix}'
        output = Path(workspace) / 'output'
        source.parent.mkdir()
        source.write_bytes(data)
        process = await asyncio.create_subprocess_exec(
            *command, '-p', str(source), '-o', str(output), '-b', settings.mineru_backend,
            '-m', settings.mineru_parse_method, stdout=PIPE, stderr=PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), settings.parse_timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise CharonError('parser_timeout') from None
        if process.returncode != 0:
            # The tail of stderr names the failing component, never the document text.
            logger.error('MinerU exited with %s: %s', process.returncode, stderr[-2000:].decode('utf-8', 'replace'))
            raise CharonError('parser_failed')
        produced = sorted(output.rglob('*.md'), key=lambda path: path.stat().st_size, reverse=True)
        if not produced:
            raise CharonError('parser_failed')
        return produced[0].read_text(encoding='utf-8')


async def parse_document(data: bytes, filename: str, *, settings: Settings) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if len(data) > settings.max_document_bytes:
        raise CharonError('document_too_large')
    if suffix in TEXT_SUFFIXES:
        parser, raw = 'text', data.decode('utf-8-sig', errors='replace')
    elif suffix in MINERU_SUFFIXES:
        parser, raw = 'mineru', await _run_mineru(data, suffix, settings)
    else:
        raise CharonError('unsupported_document')
    markdown = normalize_markdown(raw)
    if not markdown:
        raise CharonError('empty_document')
    return ParsedDocument(filename=Path(filename).name, parser=parser, markdown=markdown,
                          characters=len(markdown), fits_review_limit=len(markdown) <= REPORT_MAX_CHARACTERS)
