import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_llm
from app.core.config import get_settings
from app.llm.fake import FakeLLM

REPORT = '# Results\n\nTraining drove growth. Sales rose from 100 to 150, an increase of 80%.\n\n| Region | Q1 |\n|---|---|\n| North | 10 |\n'


@pytest.fixture
def client(settings):
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_index_page_is_served_without_a_build_step(client):
    response = client.get('/')
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/html')
    assert 'Charon' in response.text and '/review' in response.text


def test_parse_text_upload_returns_markdown(client):
    response = client.post('/parse?filename=report.txt', content=REPORT.encode(),
                           headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 200
    body = response.json()
    assert body['parser'] == 'text' and body['markdown'] == REPORT and body['fits_review_limit'] is True


@pytest.mark.parametrize('filename, status, code', [
    ('report.xlsx', 415, 'unsupported_document'),
    ('report.pdf', 503, 'parser_unavailable'),
])
def test_parse_failures_are_safe(client, settings, filename, status, code):
    settings.mineru_command = 'charon-definitely-missing-parser'
    response = client.post(f'/parse?filename={filename}', content=b'%PDF-1.4')
    assert response.status_code == status and response.json() == {'detail': {'code': code}}


def test_parse_requires_a_filename(client):
    assert client.post('/parse', content=b'x').status_code == 422


def test_preview_segments_without_model_calls(client):
    response = client.post('/preview', json={'report': REPORT})
    assert response.status_code == 200
    body = response.json()
    kinds = [unit['kind'] for unit in body['units']]
    assert kinds == ['heading', 'sentence', 'sentence', 'table_row', 'table_row']
    assert body['units'][1]['text'] == 'Training drove growth.' and body['units'][1]['tags'] == []
    assert body['units'][2]['tags'] == ['quant', 'comparison'] and body['units'][0]['tags'] == []
    assert [target['target_id'] for target in body['targets'] if target['scope'] == 'section'] == ['sec1']
    assert '[p2.s1] Training drove growth. [p2.s2 | quant, comparison] Sales rose' in body['annotated']
    assert body['characters'] == len(REPORT)


def test_preview_rejects_blank_and_oversized_reports(client):
    assert client.post('/preview', json={'report': '   '}).status_code == 422
    assert client.post('/preview', json={'report': 'x' * 30001}).status_code == 422


def test_records_are_listed_and_readable_after_a_review(client, settings):
    assert client.get('/records').json() == []
    review = client.post('/review', json={'report': REPORT, 'runs': 1})
    assert review.status_code == 200
    review_id = review.json()['review_id']
    listing = client.get('/records').json()
    assert [item['review_id'] for item in listing] == [review_id]
    assert listing[0]['provenance'] == 'offline_fake' and listing[0]['status'] == 'complete'
    record = client.get(f'/records/{review_id}')
    assert record.status_code == 200 and record.json() == review.json()


@pytest.mark.parametrize('review_id', ['missing', 'ab.json', '00000000-0000-0000-0000-000000000000'])
def test_unknown_or_malformed_record_ids_are_not_found(client, review_id):
    response = client.get(f'/records/{review_id}')
    assert response.status_code == 404 and response.json() == {'detail': {'code': 'record_not_found'}}


def test_records_listing_skips_unreadable_files(client, settings):
    settings.records_dir.mkdir(parents=True)
    (settings.records_dir / 'broken.json').write_text('{not json', encoding='utf-8')
    assert client.get('/records').json() == []
