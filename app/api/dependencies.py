"""Request-scoped model lifetime; offline tests override this dependency."""
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.llm.client import JsonLLM, LLM
from app.llm.fake import FakeLLM


async def get_llm(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[LLM]:
    if settings.charon_offline:
        yield FakeLLM(max_calls=settings.charon_max_calls)
        return
    llm = JsonLLM(settings)
    try:
        yield llm
    finally:
        await llm.aclose()
