import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError
from pydantic import SecretStr

from app.llm.client import JsonLLM
from app.llm.fake import FakeLLM
from app.schemas import FixedInput, ReviewRequest
from eval import run as evaluation
from eval.metrics import AnswerIssue, AnswerKey

REPORT = 'Training drove growth.\n\nThe budget states 60 + 50 = 100.'


def test_failed_injected_provider_attempt_exhausts_command_budget(settings, monkeypatch):
    attempts = []

    async def timeout(**_kwargs):
        attempts.append(True)
        raise APITimeoutError(request=httpx.Request('POST', 'https://example.invalid'))

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=timeout)))
    monkeypatch.setattr(evaluation, 'JsonLLM', lambda bounded, max_calls: JsonLLM(bounded, client=sdk, max_calls=max_calls))
    settings.charon_allow_live = True
    settings.kimi_api_key = SecretStr('test-placeholder')
    settings.charon_max_calls = 1
    settings.charon_max_retries = 0
    report = (evaluation.SYNTHETIC / 'report.txt').read_text(encoding='utf-8')
    result = asyncio.run(evaluation.evaluate(report, mode='end_to_end', runs=3, settings=settings, live=True))
    assert result['metrics']['valid_runs'] == 0 and result['metrics']['failed_runs'] == 3
    assert result['provenance'] == 'not_measured'
    assert result['usage']['calls'] == len(attempts) == 1
    assert result['usage']['input_tokens'] is None and result['usage']['output_tokens'] is None
    assert result['remaining_command_calls'] == 0
    assert result['evaluation_runs'] == []
    assert len(result['records']) == 3


@pytest.mark.parametrize(('mode', 'record_count'), [('fixed', 1), ('end_to_end', 3)])
def test_offline_evaluation_modes_preserve_native_run_identity(settings, mode, record_count):
    from app.agent.graph import run_review

    baseline = asyncio.run(run_review(ReviewRequest(report=REPORT), settings=settings, llm=FakeLLM()))
    fixed = FixedInput(claims=baseline.claims, evidence=baseline.evidence) if mode == 'fixed' else None
    result = asyncio.run(evaluation.evaluate(REPORT, mode=mode, runs=3, settings=settings, fixed_input=fixed))
    assert result['provenance'] == 'offline_fake' and result['mode'] == mode
    assert result['metrics']['complete_batch'] is True and result['metrics']['valid_runs'] == 3
    assert [run['run_index'] for run in result['evaluation_runs']] == ([0, 1, 2] if mode == 'fixed' else [0, 0, 0])
    assert len(result['records']) == record_count
    assert all(record['status'] == 'complete' and record['mode'] == mode for record in result['records'])
    assert result['usage']['input_tokens'] is None and result['usage']['output_tokens'] is None
    assert result['usage']['calls'] == sum(record['usage']['calls'] for record in result['records'])
    assert result['metrics']['recall']['value'] is None


def test_fixed_and_end_to_end_costs_keep_preparation_distinct(settings):
    from app.agent.graph import run_review

    baseline = asyncio.run(run_review(ReviewRequest(report=REPORT), settings=settings, llm=FakeLLM()))
    fixed = FixedInput(claims=baseline.claims, evidence=baseline.evidence)
    fixed_result = asyncio.run(evaluation.evaluate(REPORT, mode='fixed', runs=3, settings=settings, fixed_input=fixed))
    full_result = asyncio.run(evaluation.evaluate(REPORT, mode='end_to_end', runs=3, settings=settings))
    assert full_result['usage']['calls'] - fixed_result['usage']['calls'] == 6
    with pytest.raises(ValueError, match='Fixed mode requires'):
        asyncio.run(evaluation.evaluate(REPORT, mode='fixed', settings=settings))
    with pytest.raises(ValueError, match='omit'):
        asyncio.run(evaluation.evaluate(REPORT, mode='end_to_end', settings=settings, fixed_input=fixed))


def test_answer_excerpt_must_belong_to_the_current_target(settings):
    good = AnswerKey(issues=[AnswerIssue(check_id='causal_language', target_id='p1.s1',
                                         excerpt='drove growth', description='Synthetic draft.')])
    evaluation._validate_human_inputs(REPORT, good, [], settings=settings, variant='specific')
    wrong = good.model_copy(deep=True)
    wrong.issues[0].excerpt = '60 + 50 = 100'
    with pytest.raises(ValueError, match='excerpt'):
        evaluation._validate_human_inputs(REPORT, wrong, [], settings=settings, variant='specific')
    assert good.status == 'draft'


def test_cli_no_verify_does_not_expand_comparison_matrix(settings, monkeypatch, tmp_path):
    report_path = tmp_path / 'report.md'
    report_path.write_text(REPORT)
    monkeypatch.setattr(evaluation, 'Settings', lambda **_kwargs: settings)
    args = evaluation._parser().parse_args(['--report', str(report_path), '--runs', '1', '--compare', '--no-verify'])
    result = asyncio.run(evaluation._run_cli(args))
    assert result['complete'] and len(result['results']) == 4
    assert {(cell['variant'], cell['tools_enabled']) for cell in result['results']} == {
        ('specific', True), ('specific', False), ('vague', True), ('vague', False),
    }
    assert all(cell['verify_enabled'] is False for cell in result['results'])
    assert all(cell['verification_status_counts']['not_run'] == 1 for cell in result['results'])


def test_legacy_bundled_inputs_fail_with_migration_guidance():
    with pytest.raises(ValueError, match='sentence IDs'):
        evaluation._read_fixed(evaluation.SYNTHETIC / 'fixed_input.json')
    with pytest.raises(ValueError, match='keep status draft'):
        evaluation._read_answers(evaluation.SYNTHETIC / 'answers.draft.json')
