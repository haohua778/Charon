import pytest

from app.tools.detectors import arithmetic_equation, missing_reference, percent_change


@pytest.mark.parametrize(('text', 'flagged'), [
    ('Budget: 60 + 50 = 110.', False),
    ('Budget: 60 + 50 = 100.', True),
    ('Budget: $1,000 + $500 = $1,500.', False),
    ('Budget: 1.10 + 2.20 = 3.30.', False),
    ('Budget: 49.5 + 50 = 100.', False),
    ('Budget: 49.49 + 50 = 100.', True),
    ('Budget: 49.6 + 50 = 100.00.', True),
    ('Budget: 20% + 30% = 50%.', False),
])
def test_arithmetic_and_tolerance_boundaries(text, flagged):
    flags = arithmetic_equation(text)
    assert bool(flags) is flagged
    assert all(quote in text and reason for quote, reason in flags)


@pytest.mark.parametrize(('text', 'flagged'), [
    ('Revenue increased from 100 to 120 by 20%.', False),
    ('Revenue increased from 100 to 120 by 25%.', True),
    ('Revenue decreased from 100 to 80 by 20%.', False),
    ('Revenue increased from 100 to 80 by 20%.', True),
    ('Revenue rose from 100 to 120 by 20.5%.', False),
    ('Revenue rose from 100 to 120 by 20.51%.', True),
    ('Revenue rose from 0 to 120 by 20%.', False),
    ('Revenue rose from 100 to 120 by 20%, then 30%.', False),
    ('Revenue rose from 100 to 120 and from 80 to 96 by 20%.', False),
    ('Revenue was from 100 to 120 at 25%.', False),
])
def test_percentage_direction_and_unambiguous_patterns(text, flagged):
    flags = percent_change(text)
    assert bool(flags) is flagged
    assert all(quote in text and reason for quote, reason in flags)


@pytest.mark.parametrize(('report', 'flagged'), [
    ('See Table 2 for detail.\n\n# Table 2: Results\nData.', False),
    ('See Table 2 for detail.\n\nTable 2. Results\nData.', False),
    ('See Table 2 for detail.\n\n## Table 20: Results\nData.', True),
    ('See Table 2 for detail.\n\nA sentence mentions Table 2.', True),
    ('See Appendix A for detail.\n\n# Appendix A: Methods\nData.', False),
    ('See Figure 3 for detail.', True),
])
def test_missing_references_require_heading_or_caption(report, flagged):
    text = report.split('\n')[0]
    flags = missing_reference(text, report)
    assert bool(flags) is flagged
    assert all(quote in text and reason for quote, reason in flags)


@pytest.mark.parametrize(('report', 'flagged'), [
    ('Table 3 shows revenue.', True),
    ('Table 3 shows revenue.\n\n# Table 3 shows revenue', False),
    ('See Table 3.\n\nTable 3: Revenue', False),
    ('See Table 3.\n\nTABLE   3 — Revenue', False),
    ('See Table 3.\n\nTable 30: Revenue', True),
])
def test_reference_sentences_are_not_mistaken_for_caption_definitions(report, flagged):
    assert bool(missing_reference(report.split('\n')[0], report)) is flagged


@pytest.mark.parametrize('text', [
    'The total is 60 - 50 + 10 = 20.',
    'The total is 2 * 3 + 4 = 10.',
    'The total is 60 / 3 + 10 = 30.',
    'The total is (60 - 50) + 10 + 10 = 30.',
    'The total is 1K + 200 + 300 = 1,500.',
    'The total is −100 + 50 + 50 = 0.',
    'The total is −60 + 50 + 20 = 10.',
    'The total is 60 + 50 = 100 + 10.',
])
def test_arithmetic_never_reports_a_truncated_part_of_a_larger_expression(text):
    assert arithmetic_equation(text) == []


@pytest.mark.parametrize('text', [
    'The total is $1 + €2 = $3.40.',
    'The total is £1 + $2 = €3.40.',
])
def test_arithmetic_skips_mixed_explicit_currencies(text):
    assert arithmetic_equation(text) == []


@pytest.mark.parametrize('text', [
    'Revenue rose from $100 to €120 by 40%.',
    'Revenue rose from $1 to €2 by 140%.',
    'Revenue fell from £100 to $80 by 40%.',
])
def test_percent_change_skips_endpoints_with_different_currencies(text):
    assert percent_change(text) == []


@pytest.mark.parametrize('label', ['Table 3.1', 'Figure 1,000', 'Appendix X-Y'])
@pytest.mark.parametrize('has_caption', [False, True])
def test_compound_references_are_not_truncated_to_supported_label_prefixes(label, has_caption):
    sentence = f'See {label} for detail.'
    report = sentence + (f'\n\n# {label}: Results' if has_caption else '')
    assert missing_reference(sentence, report) == []


def test_unsupported_compound_reference_does_not_hide_a_separate_simple_reference():
    report = 'See Table 3.1 and Table 4 for detail.'
    findings = missing_reference(report, report)
    assert [quote for quote, _reason in findings] == ['Table 4']
    assert all(quote in report and reason for quote, reason in findings)


def test_supported_signed_same_currency_addition_keeps_exact_quote():
    assert arithmetic_equation('The total is -$100 + $50 + $50 = $0.') == []
    report = 'The total is $1,000 + $500 = $1,400.'
    findings = arithmetic_equation(report)
    assert len(findings) == 1 and findings[0][0] == '$1,000 + $500 = $1,400'
    assert findings[0][0] in report and findings[0][1]


@pytest.mark.parametrize('sentence', [
    'See the appendix below for methods.',
    'See Appendix above for methods.',
    'The Appendix section gives details.',
    'See Appendix Methods for detail.',
])
def test_appendix_prose_words_are_not_treated_as_reference_identifiers(sentence):
    assert missing_reference(sentence, sentence) == []


@pytest.mark.parametrize('identifier', ['A', 'IV', 'XII', '12'])
def test_supported_appendix_identifiers_keep_missing_reference_detection(identifier):
    sentence = f'See appendix {identifier} for methods.'
    assert [quote for quote, _reason in missing_reference(sentence, sentence)] == [f'appendix {identifier}']
    assert missing_reference(sentence, sentence + f'\n\n# Appendix {identifier}: Methods') == []


@pytest.mark.parametrize(('text', 'expected'), [
    ('Revenue grew from 100 to 130 by 20%.', '30'),
    ('Revenue fell from 100 to 80 by 25%.', '-20'),
    ('Revenue grew from 3 to 4 by 30%.', '33.3333'),
    ('Revenue grew from 100000 to 100001 by 10%.', '0.001'),
])
def test_percentage_reason_uses_readable_fixed_point_without_changing_detection(text, expected):
    finding, = percent_change(text)
    quote, reason = finding
    assert quote in text and reason.endswith(f'recalculation gives {expected}%.')


@pytest.mark.parametrize('text', ['(2)3 + 4 = 10.', '(2) 3 + 4 = 10.'])
def test_numeric_parentheses_do_not_allow_partial_implicit_multiplication(text):
    assert arithmetic_equation(text) == []


def test_textual_currency_annotation_does_not_hide_supported_addition():
    report = 'Budget (USD) 60 + 50 = 100.'
    finding, = arithmetic_equation(report)
    assert finding[0] == '60 + 50 = 100' and finding[0] in report


@pytest.mark.parametrize('text', [
    'The share is 10% + 0.2 = 0.3.',
    'The share is 0.1 + 20% = 0.3.',
    'The share is 0.1 + 0.2 = 30%.',
    'The share is 10% + 20% = 30.',
])
def test_arithmetic_skips_mixed_percentage_and_plain_number_units(text):
    assert arithmetic_equation(text) == []


@pytest.mark.parametrize(('total', 'flagged'), [('30%', False), ('40%', True)])
def test_uniform_percentage_arithmetic_keeps_correct_and_incorrect_results(total, flagged):
    text = f'The share is 10% + 20% = {total}.'
    findings = arithmetic_equation(text)
    assert bool(findings) is flagged
    assert all(quote == f'10% + 20% = {total}' and quote in text and reason
               for quote, reason in findings)


@pytest.mark.parametrize('text', [
    '**60+50=100**',
    '*60+50=100*',
    '60 + 50 = 100 (see note).',
])
def test_markdown_emphasis_and_explanatory_notes_do_not_hide_arithmetic_errors(text):
    finding, = arithmetic_equation(text)
    quote, reason = finding
    assert quote.replace(' ', '') == '60+50=100' and quote in text and reason


@pytest.mark.parametrize('text', [
    'Revenue rose from 100 to 120 (25%).',
    'Revenue rose from 100 to 120 (a 25% increase).',
    'Revenue rose from 100 to 120 - a 25% increase.',
])
def test_parenthetical_and_dash_percentage_annotations_still_detect_wrong_changes(text):
    finding, = percent_change(text)
    quote, reason = finding
    assert quote in text and '25%' in quote
    assert reason.endswith('recalculation gives 20%.')
