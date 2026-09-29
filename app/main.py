from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.documents import router as documents_router
from app.api.errors import charon_error_handler, configuration_error_handler, validation_error_handler
from app.api.review import router as review_router
from app.core.errors import CharonError

app = FastAPI(title='Charon')
app.add_exception_handler(CharonError, charon_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(ValidationError, configuration_error_handler)
app.include_router(review_router)
app.include_router(documents_router)
