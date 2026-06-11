"""One error envelope everywhere (CLAUDE.md §13):
{"error": {"code": ..., "message": ..., "detail": {}}}"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def error_envelope(code: str, message: str, detail: dict | list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, detail: dict | list | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # §13: 422 with the Pydantic error detail, in the standard envelope.
        return JSONResponse(
            status_code=422,
            content=error_envelope("validation_error", "request validation failed", exc.errors()),
        )
