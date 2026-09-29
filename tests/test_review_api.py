import json

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_llm
from app.core.config import get_settings
from app.core.errors import CharonError
from app.llm.fake import FakeLLM, demo_response
from app.schemas import ReviewRecord

REPORT = 'Training drove growth.\n\nThe budget states 60 + 50 = 100.\n\nWe guarantee increased returns.'


@pytest.fixture
def client(settings):
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def saved_records(settings):
    return [ReviewRecord.model_validate_json(path.read_text())
            for path in settings.records_dir.glob('*.json')]


def test_api_returns_complete_record_with_pending_human_decisions(client, settings):
    responses = [client.post('/review', json={'report': REPORT}) for _ in range(2)]
    assert all(response.status_code == 200 for response in responses)
    bodies = [response.json() for response in responses]
    assert bodies[0]['review_id'] != bodies[1]['review_id']
    records = saved_records(settings)
    assert len(records) == 2
    for body in bodies:
        record = next(item for item in records if item.review_id == body['review_id'])
        assert body == record.model_dump(mode='json')
        assert record.status == 'complete' and record.human_status == 'pending'
        assert record.decisions == [] and record.report == REPORT
        assert record.claims and record.evidence and record.flags
        assert record.provenance == 'offline_fake'
        assert record.analysis_type == 'program_evaluation'
        assert record.verify_enabled is True
        assert record.usage.input_tokens is None and record.usage.output_tokens is None
        severities = {check.check_id: check.severity for check in record.rubric.checks}
        for flag in record.flags:
            assert flag.severity == severities[flag.check_id]
            assert flag.confidence == (None if flag.origin == 'code' else 1)
            if flag.quote:
                assert flag.quote in REPORT[flag.target.start:flag.target.end]


@pytest.mark.parametrize(('stage', 'code'), [('extract', 'invalid_model_output'), ('evidence', 'invalid_model_output'), ('assess', 'invalid_assessment_coverage')])
def test_invalid_model_schema_fails_visibly_and_persists_record(client, settings, stage, code):
    from app.main import app

    def invalid_output(current_stage, payload):
        return {} if current_stage == stage else demo_response(current_stage, payload)

    app.dependency_overrides[get_llm] = lambda: FakeLLM(invalid_output)
    response = client.post('/review', json={'report': REPORT})
    assert response.status_code == 502
    record, = saved_records(settings)
    assert record.status == 'failed'
    assert response.json()['detail'] == {
        'review_id': record.review_id, 'status': 'failed', 'code': code,
    }


@pytest.mark.parametrize('payload', [
    {'report': 'SECRET_REPORT', 'runs': 'SECRET_INVALID_VALUE'},
    {'report': 'SECRET_REPORT', 'SECRET_UNKNOWN_FIELD': 'SECRET_VALUE'},
    {'report': ' ', 'thread_id': 'SECRET/INVALID'},
    {'report': 'SECRET_REPORT', 'thread_id': 'removed-field'},
])
def test_invalid_requests_never_echo_sensitive_input(client, payload):
    response = client.post('/review', json=payload)
    assert response.status_code == 422
    assert response.json() == {'detail': {'code': 'invalid_request'}}
    assert 'SECRET' not in response.text


def test_upstream_timeout_is_safe_and_links_failed_record(client, settings):
    from app.main import app

    def timeout(_stage, _payload):
        raise CharonError('upstream_timeout')

    app.dependency_overrides[get_llm] = lambda: FakeLLM(timeout)
    response = client.post('/review', json={'report': 'SECRET_REPORT'})
    assert response.status_code == 504 and 'SECRET_REPORT' not in response.text
    record, = saved_records(settings)
    assert response.json()['detail'] == {
        'review_id': record.review_id, 'status': 'failed', 'code': 'upstream_timeout',
    }


@pytest.mark.parametrize(('allow_live', 'code'), [
    (False, 'live_calls_disabled'), (True, 'missing_api_key'),
])
def test_unconfigured_model_dependency_fails_without_network(client, settings, allow_live, code):
    from app.main import app

    app.dependency_overrides.pop(get_llm)
    settings.charon_offline = False
    settings.charon_allow_live = allow_live
    settings.kimi_api_key = None
    response = client.post('/review', json={'report': REPORT})
    assert response.status_code == 503
    record, = saved_records(settings)
    assert response.json()['detail']['code'] == code and record.usage.calls == 0


def test_missing_and_malformed_rubric_have_safe_distinct_errors(client, settings, tmp_path):
    missing = client.post('/review', json={'report': REPORT, 'rubric_id': 'does_not_exist'})
    assert missing.status_code == 404
    assert missing.json() == {'detail': {'code': 'rubric_not_found'}}
    settings.rubrics_dir = tmp_path / 'rubrics'
    settings.rubrics_dir.mkdir()
    (settings.rubrics_dir / 'program_evaluation.specific.json').write_text(json.dumps({'SECRET_CONFIG': 1}))
    malformed = client.post('/review', json={'report': REPORT})
    assert malformed.status_code == 500
    assert malformed.json() == {'detail': {'code': 'invalid_rubric'}}


def test_record_write_failure_cannot_return_success(client, settings, tmp_path):
    blocked_directory = tmp_path / 'regular-file'
    blocked_directory.write_text('Synthetic file blocking the records directory')
    settings.records_dir = blocked_directory
    response = client.post('/review', json={'report': REPORT})
    assert response.status_code == 500
    assert response.json() == {'detail': {'code': 'record_write_failed'}}


def test_offline_mode_overrides_live_permission(client, settings, monkeypatch):
    import app.api.dependencies as dependencies
    from app.main import app

    def forbidden_live(*_args, **_kwargs):
        pytest.fail('Offline review must not instantiate the live model boundary')

    app.dependency_overrides.pop(get_llm)
    settings.charon_offline = True
    settings.charon_allow_live = True
    monkeypatch.setattr(dependencies, 'JsonLLM', forbidden_live)
    response = client.post('/review', json={'report': REPORT})
    assert response.status_code == 200 and response.json()['provenance'] == 'offline_fake'


def test_verification_failure_remains_visible_success(client, settings, flagging_responder):
    from app.main import app

    def invalid_verification(stage, payload):
        if stage == 'verify':
            return {'verifications': []}
        return flagging_responder(stage, payload)

    app.dependency_overrides[get_llm] = lambda: FakeLLM(invalid_verification)
    response = client.post('/review', json={'report': REPORT})
    assert response.status_code == 200
    record, = saved_records(settings)
    assert record.status == 'complete' and record.verification_status == 'failed'
    assert record.verification_error_code == 'invalid_verification_coverage'
    assert response.json()['verification_error_code'] == 'invalid_verification_coverage'


def test_openapi_has_direct_record_response_and_verification_switch(client):
    schema = client.get('/openapi.json').json()
    response = schema['paths']['/review']['post']['responses']['200']['content']['application/json']['schema']
    assert response['$ref'].endswith('/ReviewRecord')
    properties = schema['components']['schemas']['ReviewRequest']['properties']
    assert properties['verify_enabled']['default'] is True and 'thread_id' not in properties
