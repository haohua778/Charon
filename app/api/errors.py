"""Public errors contain stable codes, never provider messages or report text."""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.errors import CharonError


ERROR_STATUS = {
    'rubric_not_found': 404,
    'invalid_rubric': 500,
    'configuration_error': 500,
    'live_calls_disabled': 503,
    'llm_already_used': 500,
    'missing_api_key': 503,
    'upstream_timeout': 504,
    'upstream_error': 502,
    'empty_model_response': 502,
    'invalid_model_output': 502,
    'call_budget_exceeded': 502,
    'record_write_failed': 500,
    'internal_error': 500,
    'review_incomplete': 502,
    'record_not_found': 404,
    'invalid_request': 422,
    'unsupported_document': 415,
    'document_too_large': 413,
    'empty_document': 422,
    'parser_unavailable': 503,
    'parser_failed': 502,
    'parser_timeout': 504,
    **{code: 502 for code in (
        'invalid_claim_target', 'invalid_evidence_reference', 'invalid_evidence_quote',
        'incomplete_evidence', 'invalid_fixed_input',
        'tool_not_allowed', 'tool_round_limit', 'invalid_tool_expression',
        'invalid_assessment_coverage', 'invalid_assessment_answer',
        'invalid_assessment_quote', 'invalid_anchor_quote',
        'invalid_verification_coverage', 'invalid_verification_quote',
        'mixed_assessments_and_tools', 'incomplete_assessment',
    )},
}


def public_error_code(code: str | None) -> str:
    return code if code in ERROR_STATUS else 'internal_error'


def status_for_code(code: str | None, *, fallback: int = 500) -> int:
    return ERROR_STATUS.get(code, fallback)


async def charon_error_handler(_request: Request, exc: CharonError) -> JSONResponse:
    code = public_error_code(exc.code)
    return JSONResponse(
        status_code=status_for_code(code), content={'detail': {'code': code}},
    )


async def validation_error_handler(
    _request: Request, _exc: RequestValidationError,
) -> JSONResponse:
    # Pydantic errors can include the input and arbitrary extra-field names.
    return JSONResponse(status_code=422, content={'detail': {'code': 'invalid_request'}})


async def configuration_error_handler(_request: Request, _exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={'detail': {'code': 'configuration_error'}})
