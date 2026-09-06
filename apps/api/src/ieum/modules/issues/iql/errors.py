"""IQL 오류. 에러 응답은 위치를 반드시 포함한다 (query-language.md 4절)."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from ieum.core.exceptions import ValidationError


class IQLError(ValidationError):
    """IQL 문법·의미 오류. details 에 offset/length 가 들어간다."""

    code = "iql.invalid"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        offset: int = 0,
        length: int = 0,
        **extra: Any,
    ) -> None:
        details: dict[str, Any] = {"offset": offset, "length": length, **extra}
        super().__init__(message, code=code or self.code, details=details)


def syntax_error(message: str, *, offset: int, length: int = 1) -> IQLError:
    return IQLError(message, code="iql.syntax_error", offset=offset, length=length)


def unknown_field(name: str, known: list[str], *, offset: int, length: int) -> IQLError:
    """오타는 흔하다. 가까운 이름을 제안하면 사용자가 바로 고칠 수 있다."""
    return IQLError(
        f"알 수 없는 필드 {name!r}",
        code="iql.unknown_field",
        offset=offset,
        length=length,
        field=name,
        suggestions=get_close_matches(name, known, n=3, cutoff=0.6),
    )


def unknown_function(name: str, known: list[str], *, offset: int, length: int) -> IQLError:
    return IQLError(
        f"알 수 없는 함수 {name!r}",
        code="iql.unknown_function",
        offset=offset,
        length=length,
        function=name,
        suggestions=get_close_matches(name, known, n=3, cutoff=0.6),
    )


def operator_not_allowed(
    field_label: str, operator: str, allowed: list[str], *, offset: int, length: int
) -> IQLError:
    return IQLError(
        f"{field_label} 에는 {operator} 연산자를 쓸 수 없다",
        code="iql.operator_not_allowed",
        offset=offset,
        length=length,
        field=field_label,
        operator=operator,
        allowed=allowed,
    )


def invalid_value(
    field_label: str, message: str, *, offset: int, length: int, **extra: Any
) -> IQLError:
    return IQLError(
        f"{field_label}: {message}",
        code="iql.invalid_value",
        offset=offset,
        length=length,
        field=field_label,
        **extra,
    )


def unsupported(feature: str, *, offset: int, length: int, milestone: str) -> IQLError:
    """미지원 기능은 조용히 무시하지 않는다. 언제 되는지까지 알려준다."""
    return IQLError(
        f"{feature} 는 아직 지원하지 않는다 ({milestone} 예정)",
        code="iql.unsupported",
        offset=offset,
        length=length,
        feature=feature,
        milestone=milestone,
    )
