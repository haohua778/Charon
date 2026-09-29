import copy

import pytest

from app.agent.rules import flag_when_matches, render_reason, validate_assessments, validate_verifications
from app.core.errors import CharonError
from app.schemas import AssessmentOutput, Flag, Rubric, UnitQuote, VerificationOutput
from app.segment import segment_report

REPORT = 'Training drove growth. A comparison was available.'


def fixture_inputs(minimal_rubric):
    rubric = Rubric.model_validate(minimal_rubric)
    units, targets = segment_report(REPORT)
    targets = [target for target in targets if target.scope == 'claim']
    rows = [
        {'check_id': 'causal_language', 'target_id': target.target_id,
         'answers': {'causal_wording': True, 'comparison': 'none'},
         'quotes': {'causal_wording': {'unit_id': target.target_id,
                                      'text': REPORT[target.start:target.end]}}}
        for target in targets
    ]
    return rubric, units, targets, rows


@pytest.mark.parametrize(('answers', 'expected'), [
    ({'causal_wording': True, 'comparison': 'none'}, True),
    ({'causal_wording': False, 'comparison': 'none'}, False),
    ({'causal_wording': True, 'comparison': 'control_group'}, False),
])
def test_rules_are_conjunctions(minimal_rubric, answers, expected):
    check = Rubric.model_validate(minimal_rubric).checks[0]
    assert flag_when_matches(check, answers) is expected


def test_reason_template_uses_answers_and_exact_quotes(minimal_rubric):
    check = Rubric.model_validate(minimal_rubric).checks[0]
    reason = render_reason(check.reason_template,
                           {'causal_wording': True, 'comparison': 'none'},
                           {'causal_wording': UnitQuote(unit_id='p1.s1', text='drove growth')})
    assert reason == 'Causal wording "drove growth" has comparison: none.'


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate'])
def test_coverage_requires_exactly_one_row_per_check_and_target(minimal_rubric, mutation):
    rubric, units, targets, rows = fixture_inputs(minimal_rubric)
    if mutation == 'missing':
        rows.pop()
    elif mutation == 'extra':
        rows.append({**copy.deepcopy(rows[0]), 'target_id': 'doc'})
    else:
        rows.append(copy.deepcopy(rows[0]))
    output = AssessmentOutput(assessments=rows)
    with pytest.raises(CharonError, match='^invalid_assessment_coverage$'):
        validate_assessments(output, REPORT, units, targets, rubric.checks)


@pytest.mark.parametrize(('mutation', 'error'), [
    ('answer_missing', 'invalid_assessment_answer'),
    ('answer_extra', 'invalid_assessment_answer'),
    ('enum', 'invalid_assessment_answer'),
    ('bool', 'invalid_assessment_answer'),
    ('quote_missing', 'invalid_assessment_quote'),
    ('quote_invented', 'invalid_assessment_quote'),
    ('anchor_elsewhere', 'invalid_anchor_quote'),
])
def test_assessment_answers_quotes_and_anchor_validation(minimal_rubric, mutation, error):
    rubric, units, targets, rows = fixture_inputs(minimal_rubric)
    first = rows[0]
    if mutation == 'answer_missing':
        first['answers'].pop('comparison')
    elif mutation == 'answer_extra':
        first['answers']['unknown'] = True
    elif mutation == 'enum':
        first['answers']['comparison'] = 'random_choice'
    elif mutation == 'bool':
        first['answers']['causal_wording'] = 'true'
    elif mutation == 'quote_missing':
        first['quotes'] = {}
    elif mutation == 'quote_invented':
        first['quotes']['causal_wording']['text'] = 'INVENTED_TEXT'
    else:
        first['quotes']['causal_wording'] = {'unit_id': 'p1.s2', 'text': 'A comparison was available.'}
    output = AssessmentOutput(assessments=rows)
    with pytest.raises(CharonError, match=f'^{error}$'):
        validate_assessments(output, REPORT, units, targets, rubric.checks)


@pytest.mark.parametrize(('premise', 'counter', 'label'), [
    ('yes', False, 'supported'), ('partial', False, 'partially_supported'),
    ('no', False, 'premise_not_supported'), ('yes', True, 'counter_evidence_found'),
    ('no', True, 'counter_evidence_found'),
])
def test_verification_label_precedence_is_deterministic(premise, counter, label):
    from app.agent.rules import verification_results

    output = VerificationOutput(verifications=[{
        'flag_id': 'F1', 'premise_supported': premise,
        'counter': [{'unit_id': 'p1.s2', 'text': 'A comparison was available.'}] if counter else [],
    }])
    assert verification_results(output)['F1'].label == label


@pytest.mark.parametrize(('mutation', 'error'), [
    ('missing', 'invalid_verification_coverage'), ('duplicate', 'invalid_verification_coverage'),
    ('extra', 'invalid_verification_coverage'), ('quote', 'invalid_verification_quote'),
])
def test_verification_coverage_and_quote_validation(mutation, error):
    from app.schemas import AggregatedFlag

    units, targets = segment_report(REPORT)
    flag = AggregatedFlag(check_id='causal_language', dimension='interpretation', origin='model',
                          target=next(t for t in targets if t.target_id == 'p1.s1'),
                          severity='high', reason='Synthetic criticism.', quote='drove growth',
                          flag_id='F1', occurrences=1, valid_runs=1, confidence=1)
    rows = [{'flag_id': 'F1', 'premise_supported': 'yes',
             'support': [{'unit_id': 'p1.s1', 'text': 'Training drove growth.'}]}]
    if mutation == 'missing':
        rows = []
    elif mutation == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == 'extra':
        rows.append({**copy.deepcopy(rows[0]), 'flag_id': 'F2'})
    else:
        rows[0]['support'][0]['text'] = 'Invented support.'
    with pytest.raises(CharonError, match=f'^{error}$'):
        validate_verifications(VerificationOutput(verifications=rows), REPORT, units, [flag])
