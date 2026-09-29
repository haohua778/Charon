from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.agent.graph import run_review
from app.api.dependencies import get_llm
from app.api.errors import public_error_code, status_for_code
from app.core.config import Settings, get_settings
from app.llm.client import LLM
from app.schemas import ReviewRecord, ReviewRequest

router = APIRouter()


@router.post('/review', response_model=ReviewRecord)
async def review(
    request: ReviewRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> ReviewRecord:
    record = await run_review(request, llm=llm, settings=settings)
    if record.status != 'complete':
        code = record.error_code or 'review_incomplete'
        raise HTTPException(
            status_code=status_for_code(code, fallback=502),
            detail={'review_id': record.review_id, 'status': record.status,
                    'code': public_error_code(code)},
        )
    return record
