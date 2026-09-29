"""Demo support around /review: parse a document, preview segmentation, read records, serve the page."""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.errors import CharonError
from app.parsing import ParsedDocument, parse_document
from app.schemas import REPORT_MAX_CHARACTERS, Contract, ReviewRecord, Target, Unit
from app.segment import render_annotated, segment_report
from app.tools.cues import cue_tags

router = APIRouter()
INDEX_PAGE = PROJECT_ROOT / 'app' / 'static' / 'index.html'
REVIEW_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class PreviewRequest(Contract):
    report: str = Field(min_length=1, max_length=REPORT_MAX_CHARACTERS)


class PreviewUnit(Unit):
    text: str
    tags: list[str]


class PreviewResponse(Contract):
    characters: int
    units: list[PreviewUnit]
    targets: list[Target]
    annotated: str


class RecordSummary(Contract):
    review_id: str
    created_at: str
    status: str
    provenance: str
    mode: str
    flag_count: int
    elapsed_seconds: float


@router.get('/', include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_PAGE, media_type='text/html')


@router.post('/parse', response_model=ParsedDocument)
async def parse(request: Request, settings: Annotated[Settings, Depends(get_settings)],
                filename: Annotated[str, Query(min_length=1, max_length=255)]) -> ParsedDocument:
    """The raw request body is the document; no multipart dependency is needed."""
    return await parse_document(await request.body(), filename, settings=settings)


@router.post('/preview', response_model=PreviewResponse)
async def preview(request: PreviewRequest) -> PreviewResponse:
    """Free local segmentation, so an operator can inspect units before paying for a review."""
    report = request.report
    try:
        units, targets = segment_report(report)
    except ValueError as exc:
        raise CharonError('invalid_request') from exc
    return PreviewResponse(
        characters=len(report),
        units=[PreviewUnit(**unit.model_dump(), text=report[unit.start:unit.end],
                           tags=[] if unit.kind == 'heading' else cue_tags(report[unit.start:unit.end]))
               for unit in units],
        targets=targets, annotated=render_annotated(report, units),
    )


@router.get('/records', response_model=list[RecordSummary])
async def list_records(settings: Annotated[Settings, Depends(get_settings)],
                       limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[RecordSummary]:
    summaries = []
    for path in settings.records_dir.glob('*.json') if settings.records_dir.is_dir() else []:
        try:
            record = ReviewRecord.model_validate_json(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            continue
        summaries.append(RecordSummary(review_id=record.review_id, created_at=record.created_at,
                                       status=record.status, provenance=record.provenance, mode=record.mode,
                                       flag_count=len(record.flags), elapsed_seconds=record.elapsed_seconds))
    summaries.sort(key=lambda item: item.created_at, reverse=True)
    return summaries[:limit]


@router.get('/records/{review_id}', response_model=ReviewRecord)
async def read_record(review_id: str, settings: Annotated[Settings, Depends(get_settings)]) -> ReviewRecord:
    if not REVIEW_ID.fullmatch(review_id):
        raise CharonError('record_not_found')
    path = settings.records_dir / f'{review_id}.json'
    try:
        return ReviewRecord.model_validate_json(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise CharonError('record_not_found') from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise CharonError('internal_error') from exc
