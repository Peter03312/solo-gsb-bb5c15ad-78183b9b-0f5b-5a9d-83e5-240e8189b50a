"""Uniform error envelope."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details

    def response(self) -> JSONResponse:
        body: dict[str, object] = {"error": {"code": self.code, "message": self.message}}
        if self.details is not None:
            body["error"]["details"] = self.details
        return JSONResponse(status_code=self.status_code, content=body)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return exc.response()
