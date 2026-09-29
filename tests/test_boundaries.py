"""The provider boundary is exercised with an injected in-memory SDK only."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from app.core.config import Settings
from app.core.errors import CharonError
from app.llm.client import JsonLLM
from app.schemas import ExtractionOutput


def client_with(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def live_settings(**updates):
    return Settings(_env_file=None, charon_offline=False, charon_allow_live=True,
                    kimi_api_key='test-placeholder', **updates)


def response(content, finish_reason='stop', usage=None):
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content),
    )])


@pytest.mark.parametrize(('budget', 'code'), [(1, 'call_budget_exceeded'), (2, 'upstream_timeout')])
def test_provider_retries_share_physical_call_budget_and_unknown_usage(budget, code):
    attempts = []

    async def fail_create(**kwargs):
        attempts.append(kwargs)
        raise APITimeoutError(request=httpx.Request('POST', 'https://example.invalid'))

    llm = JsonLLM(live_settings(charon_max_calls=budget, charon_max_retries=1),
                  client=client_with(fail_create))
    with pytest.raises(CharonError, match=f'^{code}$'):
        asyncio.run(llm.generate('extract', {'annotated_report': 'Synthetic input'}, ExtractionOutput))
    assert llm.usage.calls == len(attempts) == budget
    assert llm.usage.input_tokens is None and llm.usage.output_tokens is None
    assert all(attempt['temperature'] == 0 for attempt in attempts)


@pytest.mark.parametrize(('finish_reason', 'content', 'valid'), [
    ('stop', '{"claims": []}', True),
    ('length', '{"claims": []}', False),
    ('content_filter', '{"claims": []}', False),
    ('stop', '{}', False),
])
def test_provider_accepts_only_complete_schema_valid_output(finish_reason, content, valid):
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return response(content, finish_reason)

    llm = JsonLLM(live_settings(), client=client_with(respond))
    if valid:
        assert asyncio.run(llm.generate('extract', {}, ExtractionOutput)).claims == []
    else:
        with pytest.raises(CharonError, match='^invalid_model_output$'):
            asyncio.run(llm.generate('extract', {}, ExtractionOutput))
    assert llm.usage.calls == len(calls) <= 2
    assert llm.usage.input_tokens is None and llm.usage.output_tokens is None


def test_schema_repair_is_single_budgeted_attempt_and_includes_previous_output():
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return response('{"SECRET_EXTRA": "REPORT_MARKER"}' if len(calls) == 1 else '{"claims": []}')

    llm = JsonLLM(live_settings(), client=client_with(respond))
    result = asyncio.run(llm.generate('extract', {'annotated_report': 'REPORT_MARKER'}, ExtractionOutput))
    assert result.claims == [] and llm.usage.calls == 2
    repair = json.loads(calls[1]['messages'][1]['content'])['repair']
    assert repair['previous_output'] == '{"SECRET_EXTRA": "REPORT_MARKER"}'
    errors = json.dumps(repair['errors'])
    assert 'REPORT_MARKER' not in errors and 'SECRET_EXTRA' not in errors
    assert '<field>' in errors


def test_business_validation_retries_exactly_once_and_preserves_error_code():
    attempts = []

    async def respond(**kwargs):
        attempts.append(kwargs)
        return response('{"claims": []}')

    def reject(_value):
        raise CharonError('invalid_claim_target')

    llm = JsonLLM(live_settings(), client=client_with(respond))
    with pytest.raises(CharonError, match='^invalid_claim_target$'):
        asyncio.run(llm.generate('extract', {}, ExtractionOutput, validator=reject))
    assert llm.usage.calls == len(attempts) == 2


def test_repair_cannot_bypass_explicit_client_call_budget():
    async def respond(**_kwargs):
        return response('{}')

    llm = JsonLLM(live_settings(), client=client_with(respond), max_calls=1)
    with pytest.raises(CharonError, match='^call_budget_exceeded$'):
        asyncio.run(llm.generate('extract', {}, ExtractionOutput))
    assert llm.usage.calls == 1


def test_offline_guard_blocks_injected_provider_even_when_live_is_allowed():
    async def forbidden_create(**_kwargs):
        pytest.fail('Offline guard must run before any provider attempt')

    config = Settings(_env_file=None, charon_offline=True, charon_allow_live=True,
                      kimi_api_key='test-placeholder')
    llm = JsonLLM(config, client=client_with(forbidden_create))
    with pytest.raises(CharonError, match='^live_calls_disabled$'):
        asyncio.run(llm.generate('extract', {}, ExtractionOutput))
    assert llm.usage.calls == 0


def test_parallel_model_calls_share_one_physical_budget():
    attempts = []

    async def respond(**kwargs):
        attempts.append(kwargs)
        await asyncio.sleep(0)
        return response('{"claims": []}')

    llm = JsonLLM(live_settings(), client=client_with(respond), max_calls=2)

    async def run_parallel():
        return await asyncio.gather(*(llm.generate('extract', {}, ExtractionOutput) for _ in range(3)),
                                    return_exceptions=True)

    outputs = asyncio.run(run_parallel())
    assert len(attempts) == llm.usage.calls == 2
    failures = [value for value in outputs if isinstance(value, CharonError)]
    assert len(failures) == 1 and failures[0].code == 'call_budget_exceeded'


def test_usage_stays_unknown_after_any_response_omits_counts():
    usages = [SimpleNamespace(prompt_tokens=3, completion_tokens=4), None,
              SimpleNamespace(prompt_tokens=5, completion_tokens=6)]

    async def respond(**_kwargs):
        return response('{"claims": []}', usage=usages.pop(0))

    llm = JsonLLM(live_settings(), client=client_with(respond))
    for _ in range(3):
        asyncio.run(llm.generate('extract', {}, ExtractionOutput))
    assert llm.usage.calls == 3
    assert llm.usage.input_tokens is None and llm.usage.output_tokens is None
