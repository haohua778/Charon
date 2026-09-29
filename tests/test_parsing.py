import asyncio
import shlex
import stat
from pathlib import Path

import pytest

from app.core.errors import CharonError
from app.parsing import normalize_markdown, parse_document


def parse(data, filename, *, settings):
    return asyncio.run(parse_document(data, filename, settings=settings))


def test_html_tables_become_pipe_tables_and_images_are_dropped():
    raw = ('# Title\r\n\r\nSales rose. ![](images/a.jpg)\r\n<html><body><table><tr><td>Region</td><td>Q1</td></tr>'
           '<tr><td>North<br>East</td><td>1 | 2</td></tr></table></body></html>\r\n\r\n\r\n\r\nEnd.  \r\n')
    assert normalize_markdown(raw) == (
        '# Title\n\nSales rose.\n\n| Region | Q1 |\n| --- | --- |\n| North East | 1 \\| 2 |\n\nEnd.\n'
    )


def test_nested_tables_are_flattened_into_one_pipe_table():
    raw = ('Before\n<table><tr><td>Outer</td><td><table><tr><td>in1</td></tr><tr><td>in2</td></tr></table></td></tr>'
           '<tr><td><p>x</p></td><td>y</td></tr></table>\nAfter <1.0 units\n')
    assert normalize_markdown(raw) == (
        'Before\n\n| Outer | in1 in2 |\n| --- | --- |\n| x | y |\n\nAfter <1.0 units\n'
    )


def test_inline_markup_and_links_are_unwrapped_but_text_is_kept():
    raw = ('Lane<sup>a,c,*</sup> found <strong>growth</strong> of 5%<br>next.\n\n'
           '- [**1.** **Scope**](#_Toc1)\n- <a href="#x"><em>Method</em></a>\n\nSee [Table 3](#t3).\n')
    assert normalize_markdown(raw) == (
        'Lanea,c,* found growth of 5%next.\n\n- **1.** **Scope**\n- Method\n\nSee Table 3.\n'
    )


def test_normalize_keeps_plain_markdown_and_handles_blank_input():
    assert normalize_markdown('﻿Plain **text** with $x$.\n') == 'Plain **text** with $x$.\n'
    assert normalize_markdown('  \n\n') == ''


def test_text_documents_are_decoded_without_a_parser(settings):
    document = parse('Sales rose from 100 to 150.\n'.encode(), 'note.MD', settings=settings)
    assert document.parser == 'text' and document.fits_review_limit
    assert document.markdown == 'Sales rose from 100 to 150.\n'
    assert document.characters == len(document.markdown)


@pytest.mark.parametrize('filename, payload, code', [
    ('report.xlsx', b'x', 'unsupported_document'),
    ('report', b'x', 'unsupported_document'),
    ('report.txt', b'  \n', 'empty_document'),
])
def test_rejected_documents_use_stable_codes(settings, filename, payload, code):
    with pytest.raises(CharonError) as info:
        parse(payload, filename, settings=settings)
    assert info.value.code == code


def test_oversized_documents_are_rejected_before_parsing(settings):
    settings.max_document_bytes = 4
    with pytest.raises(CharonError) as info:
        parse(b'12345', 'report.txt', settings=settings)
    assert info.value.code == 'document_too_large'


@pytest.mark.parametrize('command', [
    'charon-definitely-missing-parser',
    '/definitely/missing/.venv/bin/python scripts/mineru_text_parse.py',
    'sh /definitely/missing/mineru_text_parse.py',
])
def test_missing_mineru_command_or_script_is_a_safe_error(settings, command):
    settings.mineru_command = command
    with pytest.raises(CharonError) as info:
        parse(b'%PDF-1.4', 'report.pdf', settings=settings)
    assert info.value.code == 'parser_unavailable'


def fake_mineru(tmp_path: Path, body: str) -> str:
    """A shell stand-in that receives the same -p/-o/-b/-m arguments as the real wrapper."""
    script = tmp_path / 'fake mineru.sh'
    script.write_text('#!/bin/sh\nall="$*"\nout=""\nwhile [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; esac; shift; done\n' + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return shlex.quote(str(script))


def test_mineru_output_is_discovered_and_normalized(settings, tmp_path):
    settings.mineru_command = fake_mineru(tmp_path, (
        'mkdir -p "$out/document/auto"\n'
        'printf "# Summary\\n\\n<table><tr><td>A</td><td>1</td></tr></table>\\n" > "$out/document/auto/document.md"\n'
    ))
    document = parse(b'%PDF-1.4', 'Client Report.pdf', settings=settings)
    assert document.parser == 'mineru' and document.filename == 'Client Report.pdf'
    assert document.markdown == '# Summary\n\n| A | 1 |\n| --- | --- |\n'
    assert parse(b'PK', 'Client Report.docx', settings=settings).parser == 'mineru'


def test_arguments_reach_the_wrapper(settings, tmp_path):
    settings.mineru_command = fake_mineru(tmp_path, (
        'mkdir -p "$out"\nprintf "%s\\n" "$all" > "$out/args.md"\n'
    ))
    settings.mineru_parse_method = 'auto'
    document = parse(b'%PDF-1.4', 'r.pdf', settings=settings)
    words = document.markdown.split()
    assert words[0] == '-p' and words[1].endswith('/input/document.pdf')
    assert words[2] == '-o' and words[4:] == ['-b', 'pipeline', '-m', 'auto']


def test_mineru_failures_map_to_codes(settings, tmp_path):
    settings.mineru_command = fake_mineru(tmp_path, 'echo "layout model crashed" >&2\nexit 3\n')
    with pytest.raises(CharonError) as failed:
        parse(b'%PDF-1.4', 'r.pdf', settings=settings)
    assert failed.value.code == 'parser_failed'

    settings.mineru_command = fake_mineru(tmp_path, 'mkdir -p "$out"\nexit 0\n')
    with pytest.raises(CharonError) as empty:
        parse(b'%PDF-1.4', 'r.pdf', settings=settings)
    assert empty.value.code == 'parser_failed'

    settings.mineru_command = fake_mineru(tmp_path, 'sleep 5\n')
    settings.parse_timeout_seconds = 0.2
    with pytest.raises(CharonError) as slow:
        parse(b'%PDF-1.4', 'r.pdf', settings=settings)
    assert slow.value.code == 'parser_timeout'

