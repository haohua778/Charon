"""Source locations are character spans; fixture text is synthetic English."""
import pytest

from app.core.errors import CharonError
from app.segment import claim_from_range, render_annotated, resolve_target, segment_report


def sentence_texts(report):
    units, _ = segment_report(report)
    return [report[unit.start:unit.end] for unit in units if unit.kind == 'sentence']


@pytest.mark.parametrize(('report', 'sentences'), [
    ('The rate is 3.5%. Sales rose.', ['The rate is 3.5%.', 'Sales rose.']),
    ('We paused... Then resumed.', ['We paused... Then resumed.']),
    ('Dr. J. Smith spoke. Revenue rose.', ['Dr. J. Smith spoke.', 'Revenue rose.']),
    ('The U.S. office of Example Inc. grew. Sales rose.',
     ['The U.S. office of Example Inc. grew.', 'Sales rose.']),
    ('Use e.g. Sample Co. for comparison. Revenue rose.',
     ['Use e.g. Sample Co. for comparison.', 'Revenue rose.']),
    ('The result was "good." Next came review.', ['The result was "good."', 'Next came review.']),
    ('The result was (good!) Next came review.', ['The result was (good!)', 'Next came review.']),
    ('One result? 2025 followed.', ['One result?', '2025 followed.']),
    ('Revenue rose, because demand grew: no sentence split. More followed.',
     ['Revenue rose, because demand grew: no sentence split.', 'More followed.']),
    ('  The café grew 🙂.  Next result.  ', ['The café grew 🙂.', 'Next result.']),
    ('One result. lower case continuation.', ['One result. lower case continuation.']),
])
def test_english_sentence_boundaries(report, sentences):
    assert sentence_texts(report) == sentences


@pytest.mark.parametrize('abbreviation', [
    'e.g.', 'i.e.', 'etc.', 'vs.', 'Mr.', 'Mrs.', 'Ms.', 'Dr.', 'Inc.', 'Ltd.',
    'Co.', 'Corp.', 'No.', 'Fig.', 'approx.', 'U.S.', 'U.K.', 'Jan.', 'Feb.',
    'Mar.', 'Apr.', 'Jun.', 'Jul.', 'Aug.', 'Sep.', 'Oct.', 'Nov.', 'Dec.',
])
def test_required_abbreviations_do_not_end_sentences(abbreviation):
    report = f'We cite {abbreviation} Sample here. Another sentence.'
    assert sentence_texts(report) == [f'We cite {abbreviation} Sample here.', 'Another sentence.']


def test_headings_sections_lists_tables_and_exact_unicode_offsets():
    report = ('Intro café.\n\n# Results\nOne result. Another result.\n\n'
              '- First list item. Second sentence.\n- Next item.\n\n'
              '| Period | Revenue |\n|---|---|\n| Q3 | $12 |\n\n## Next\nLast result.')
    units, targets = segment_report(report)
    assert (units, targets) == segment_report(report)
    assert len({unit.unit_id for unit in units}) == len(units)
    assert all(report[u.start:u.end] == report[u.start:u.end].strip() for u in units)
    assert len([u for u in units if u.kind == 'heading']) == 2
    assert [report[u.start:u.end] for u in units if u.kind == 'table_row'] == [
        '| Period | Revenue |', '| Q3 | $12 |',
    ]
    assert [t.target_id for t in targets if t.scope == 'section'] == ['sec0', 'sec1', 'sec2']
    assert all(u.section_id == 'sec0' for u in units if u.paragraph == 1)
    sections = {t.target_id: report[t.start:t.end] for t in targets if t.scope == 'section'}
    assert sections['sec1'].startswith('# Results') and '## Next' not in sections['sec1']
    assert sections['sec2'].startswith('## Next')
    assert len([t for t in targets if t.scope == 'claim']) == len([u for u in units if u.kind == 'sentence'])
    document = next(t for t in targets if t.target_id == 'doc')
    assert report[document.start:document.end] == report
    rendered = render_annotated(report, units)
    assert '[sec1]' in rendered and '[sec2]' in rendered
    assert '"start"' not in rendered and '"end"' not in rendered


def test_range_targets_preserve_intervening_whitespace_and_claim_identity():
    report = 'First sentence.  Second sentence.\nThird sentence.\n\nNext paragraph.'
    units, _ = segment_report(report)
    claim = claim_from_range(report, units, 'p1.s1', 'p1.s3')
    assert claim.claim_id == claim.target.target_id == 'p1.s1-s3'
    assert claim.unit_ids == ['p1.s1', 'p1.s2', 'p1.s3']
    assert claim.section_id == 'sec0'
    assert claim.text == 'First sentence.  Second sentence.\nThird sentence.'
    assert resolve_target(report, claim.claim_id, units) == claim.target


@pytest.mark.parametrize(('first', 'last'), [
    ('p1.s1', 'p2.s1'), ('p1.s2', 'p1.s1'), ('missing', 'p1.s1'),
])
def test_invalid_claim_ranges_are_rejected(first, last):
    report = 'First sentence. Second sentence.\n\nNext paragraph.'
    units, _ = segment_report(report)
    with pytest.raises(CharonError, match='^invalid_claim_target$'):
        claim_from_range(report, units, first, last)


def test_headings_and_table_rows_cannot_be_claims():
    report = '# Heading\n\n| Item | Value |\n|---|---|\n| A | 2 |'
    units, _ = segment_report(report)
    for unit in units:
        with pytest.raises(CharonError, match='^invalid_claim_target$'):
            claim_from_range(report, units, unit.unit_id, unit.unit_id)


def test_no_heading_and_preface_section_ids_are_visible_to_the_model():
    for report in ('Opening sentence.', 'Opening sentence.\n\n# Results\nNext sentence.'):
        units, targets = segment_report(report)
        assert any(target.target_id == 'sec0' for target in targets)
        rendered = render_annotated(report, units)
        assert '[sec0]' in rendered and '[p1.s1]' in rendered
        assert rendered.index('[sec0]') < rendered.index('[p1.s1]')


def test_crlf_and_whitespace_only_blank_lines_preserve_exact_source_offsets():
    report = '  One result.\r\n \t\r\n Two results. \r\n\r\nThree results.  '
    units, _ = segment_report(report)
    assert [unit.unit_id for unit in units] == ['p1.s1', 'p2.s1', 'p3.s1']
    assert [report[unit.start:unit.end] for unit in units] == ['One result.', 'Two results.', 'Three results.']
    assert (units, segment_report(report)[1]) == segment_report(report)
