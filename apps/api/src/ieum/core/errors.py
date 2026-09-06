"""에러 응답 포맷과 전역 예외 핸들러.

포맷은 고정이다 (CLAUDE.md 8절):

    {"error": {"code": "...", "message": "...", "details": {}, "trace_id": "..."}}

`code` 만 안정 계약이다. `message` 는 디버깅용이고 사용자에게 그대로 보여주지
않는다 — 표시 문구는 클라이언트가 code 로 번역한다 (i18n 1절).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ieum.core.context import get_trace_id
from ieum.core.exceptions import IeumError, RateLimitedError
from ieum.core.logging import get_logger

log = get_logger(__name__)


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "trace_id": get_trace_id(),
        }
    }


async def _handle_domain_error(_r: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IeumError)
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, exc.details),
        headers=headers,
    )


async def _handle_request_validation(_r: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # pydantic 의 원본 오류를 details 에 넣되, 입력값(input)은 뺀다.
    # 비밀번호 같은 값이 에러 응답과 로그로 새어나가면 안 된다.
    fields = [
        {"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_body(
            "common.validation_failed", "요청 본문이 스키마에 맞지 않는다.", {"fields": fields}
        ),
    )


async def _handle_http_exception(_r: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {
        401: "auth.unauthenticated",
        403: "auth.permission_denied",
        404: "common.not_found",
        405: "common.method_not_allowed",
        429: "common.rate_limited",
    }.get(exc.status_code, "common.http_error")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """예상 못 한 예외. 내부 정보를 응답에 흘리지 않는다."""
    log.exception(
        "request.unhandled_exception",
        path=request.url.path,
        method=request.method,
        error=f"{type(exc).__name__}: {exc}",
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_body("internal.error", "내부 오류가 발생했다."),
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(IeumError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)
