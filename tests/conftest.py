from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, records_dir=tmp_path / 'records',
                    rubrics_dir=PROJECT_ROOT / 'rubrics', charon_allow_live=False,
                    charon_offline=False, charon_max_calls=80)


@pytest.fixture
def minimal_rubric():
    return {
        'rubric_id': 'program_evaluation', 'analysis_type': 'program_evaluation',
        'variant': 'specific', 'version': '0.1-draft',
        'dimensions': [{
            'dimension_id': 'interpretation', 'name': 'Interpretation',
            'checks': [{
                'check_id': 'causal_language', 'kind': 'model_assessed',
                'scope': 'claim', 'severity': 'high',
                'description': 'Causal language requires a supporting comparison.',
                'questions': [
                    {'field': 'causal_wording', 'type': 'bool',
                     'ask': 'Does this claim use causal language?', 'quote_required_when': [True]},
                    {'field': 'comparison', 'type': 'enum', 'values': ['none', 'control_group'],
                     'ask': 'What comparison supports this claim?',
                     'quote_required_when': ['control_group']},
                ],
                'anchor_field': 'causal_wording',
                'flag_when': {'causal_wording': [True], 'comparison': ['none']},
                'reason_template': 'Causal wording "{causal_wording.quote}" has comparison: {comparison}.',
                'examples': [{'text': 'Training drove growth.', 'flag': True, 'why': 'No comparison.'}],
                'tools': [],
            }],
        }],
    }


@pytest.fixture
def minimal_settings(settings, minimal_rubric, tmp_path):
    import json

    settings.rubrics_dir = tmp_path / 'rubrics'
    settings.rubrics_dir.mkdir()
    for variant in ('specific', 'vague'):
        (settings.rubrics_dir / f'program_evaluation.{variant}.json').write_text(
            json.dumps({**minimal_rubric, 'variant': variant}), encoding='utf-8',
        )
    return settings


@pytest.fixture
def flagging_responder():
    from app.llm.fake import demo_response

    def respond(stage, payload):
        output = demo_response(stage, payload)
        if stage in ('assess', 'assess_final'):
            for row in output['assessments']:
                if row['check_id'] == 'causal_language' and row['target_id'] == 'p1.s1':
                    row['answers'] = {'causal_wording': True, 'comparison': 'none'}
                    row['quotes'] = {'causal_wording': {'unit_id': 'p1.s1', 'text': 'Training drove growth.'}}
        return output

    return respond
