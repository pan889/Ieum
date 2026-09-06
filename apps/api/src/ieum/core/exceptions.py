"""도메인 예외. 서비스는 이것만 발생시키고, HTTP 변환은 전역 핸들러가 한다.

에러 응답 포맷은 고정이다 (CLAUDE.md 8절):
    {"error": {"code": "...", "message": "...", "details": {}, "trace_id": "..."}}

`code` 는 안정적인 문자열 상수이고 사용자에게 보일 문구가 아니다.
표시 문구는 클라이언트가 code 로 번역한다 (docs/architecture/i18n.md 1절).
"""

from __future__ import annotations

from typing import Any


class IeumError(Exception):
    """모든 도메인 예외의 뿌리."""

    code: str = "internal.error"
    status_code: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        # docstring 을 기본 메시지로 쓰면 여러 줄 설계 주석이 API 응답으로 샌다.
        # 첫 줄만 쓴다.
        default = (self.__class__.__doc__ or self.code).strip().split("\n")[0]
        self.message = message or default
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)


class NotFoundError(IeumError):
    """요청한 리소스가 없다."""

    code = "common.not_found"
    status_code = 404


class ConflictError(IeumError):
    """현재 상태와 충돌한다."""

    code = "common.conflict"
    status_code = 409


class ValidationError(IeumError):
    """입력이 도메인 규칙에 맞지 않는다."""

    code = "common.validation_failed"
    status_code = 422


class AuthenticationError(IeumError):
    """인증되지 않았다."""

    code = "auth.unauthenticated"
    status_code = 401


class PermissionDeniedError(IeumError):
    """권한이 없다."""

    code = "auth.permission_denied"
    status_code = 403


class MFARequiredError(IeumError):
    """2FA 를 완료해야 한다.

    미충족 세션은 MFA 등록·검증 API 외 모든 요청이 막힌다
    (docs/architecture/auth.md 3절).
    """

    code = "auth.mfa_required"
    status_code = 403


class StepUpRequiredError(IeumError):
    """민감한 작업이라 최근 MFA 재확인이 필요하다."""

    code = "auth.step_up_required"
    status_code = 403


class RateLimitedError(IeumError):
    """시도가 너무 잦다."""

    code = "common.rate_limited"
    status_code = 429

    def __init__(self, retry_after_seconds: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.retry_after_seconds = retry_after_seconds
        self.details["retry_after_seconds"] = retry_after_seconds


class OptimisticLockError(ConflictError):
    """다른 사용자가 먼저 수정했다. If-Match 불일치."""

    code = "common.version_conflict"
