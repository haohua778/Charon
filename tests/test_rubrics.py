import copy
import json

import pytest

from app.core.errors import CharonError
from app.core.rubrics import load_rubric, validate_variant_structure
from app.schemas import Rubric


@pytest.mark.parametrize('mutation', [
    'unknown_rule_field', 'unknown_rule_value', 'unknown_anchor', 'unknown_template',
    'format_conversion', 'invalid_question_type', 'standard_severity', 'too_many_checks',
])
def test_invalid_rubric_is_rejected_when_loaded(minimal_rubric, tmp_path, mutation):
    data = copy.deepcopy(minimal_rubric)
    check = data['dimensions'][0]['checks'][0]
    if mutation == 'unknown_rule_field':
        check['flag_when'] = {'unknown': [True]}
    elif mutation == 'unknown_rule_value':
        check['flag_when'] = {'comparison': ['randomized']}
    elif mutation == 'unknown_anchor':
        check['anchor_field'] = 'unknown'
    elif mutation == 'unknown_template':
        check['reason_template'] = '{unknown.quote}'
    elif mutation == 'format_conversion':
        check['reason_template'] = '{comparison!r}'
    elif mutation == 'invalid_question_type':
        check['questions'][0]['type'] = 'text'
    elif mutation == 'standard_severity':
        data['dimensions'][0]['checks'] = [{
            'check_id': 'awaiting_standard', 'kind': 'needs_standard', 'scope': 'document',
            'severity': 'high', 'description': 'Awaiting a standard.', 'pending_question': 'Which standard?',
        }]
    else:
        data['dimensions'][0]['checks'] = [
            {**copy.deepcopy(check), 'check_id': f'check_{index}'} for index in range(21)
        ]
    (tmp_path / 'program_evaluation.specific.json').write_text(json.dumps(data))
    with pytest.raises(CharonError, match='^invalid_rubric$'):
        load_rubric('program_evaluation', root=tmp_path)


def test_variant_wording_can_vary_but_decision_structure_cannot(minimal_rubric):
    specific = Rubric.model_validate(minimal_rubric)
    vague_data = copy.deepcopy(minimal_rubric)
    vague_data['variant'] = 'vague'
    check = vague_data['dimensions'][0]['checks'][0]
    check['description'] = 'Review whether the claim is reasonable.'
    check['questions'][0]['ask'] = 'Is the language strong?'
    check['examples'][0]['text'] = 'Growth happened.'
    check['examples'][0]['why'] = 'Review this.'
    vague = Rubric.model_validate(vague_data)
    validate_variant_structure(specific, vague)
    for field, value in [('severity', 'low'), ('scope', 'document'),
                         ('flag_when', {'causal_wording': [False]})]:
        changed = vague.model_copy(deep=True)
        setattr(changed.dimensions[0].checks[0], field, value)
        with pytest.raises(CharonError, match='^invalid_rubric$'):
            validate_variant_structure(specific, changed)


@pytest.mark.parametrize('analysis_type', ['program_evaluation', 'kpi_comparison'])
def test_bundled_draft_variants_have_matching_structure(analysis_type, settings):
    specific = load_rubric(analysis_type, 'specific', settings.rubrics_dir)
    vague = load_rubric(analysis_type, 'vague', settings.rubrics_dir)
    validate_variant_structure(specific, vague)
    assert specific.version == vague.version == '0.1-draft'
    assert set(check.kind for check in specific.checks) == {'code', 'model_assessed', 'needs_standard'}
    assert len(specific.checks) <= 20
