"""커스텀 필드 값 검증.

정의는 `field_definition`, 값은 `issue_field_value`(JSONB) 다. 컬럼을 추가하지
않는다 (D-18). kind 별 밸리데이터를 레지스트리로 두고, 프론트는 같은 정의를
받아 zod 스키마를 생성한다 (module-guide 커스텀 필드).

값은 JSONB 로 저장되므로 파이썬 타입이 그대로 왕복해야 한다. date 처럼
JSON 이 모르는 타입은 문자열로 정규화해서 저장한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any, NoReturn
from uuid import UUID

from ieum.core.exceptions import ValidationError

#: kind → (원시값) -> 정규화된 값. 실패하면 ValidationError.
Validator = Callable[[Any, dict[str, Any]], Any]

_validators: dict[str, Validator] = {}


def _register(kind: str) -> Callable[[Validator], Validator]:
    def decorator(fn: Validator) -> Validator:
        _validators[kind] = fn
        return fn

    return decorator


def _fail(message: str, **details: Any) -> NoReturn:
    raise ValidationError(message, code="issues.invalid_field_value", details=details)


@_register("text")
def _text(value: Any, config: dict[str, Any]) -> str:
    if not isinstance(value, str):
        _fail("문자열이어야 한다.", got=type(value).__name__)
    text = value.strip()
    max_length = int(config.get("max_length", 4000))
    if len(text) > max_length:
        _fail(f"{max_length}자 이하여야 한다.", max_length=max_length, length=len(text))
    return text


@_register("number")
def _number(value: Any, config: dict[str, Any]) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail("숫자여야 한다.", got=type(value).__name__)
    number: float | int = value
    minimum, maximum = config.get("min"), config.get("max")
    if minimum is not None and number < minimum:
        _fail(f"{minimum} 이상이어야 한다.", min=minimum)
    if maximum is not None and number > maximum:
        _fail(f"{maximum} 이하여야 한다.", max=maximum)
    return number


@_register("date")
def _date(value: Any, _config: dict[str, Any]) -> str:
    """ISO 8601 날짜 문자열로 정규화한다. JSONB 는 date 타입을 모른다."""
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        _fail("YYYY-MM-DD 형식이어야 한다.", got=type(value).__name__)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        _fail("YYYY-MM-DD 형식이어야 한다.", value=value)
        raise  # pragma: no cover - _fail 이 항상 던진다


@_register("select")
def _select(value: Any, config: dict[str, Any]) -> str:
    options = [str(o) for o in config.get("options", [])]
    if not isinstance(value, str) or value not in options:
        _fail("허용되지 않은 선택지다.", options=options, value=value)
    return value


@_register("multi_select")
def _multi_select(value: Any, config: dict[str, Any]) -> list[str]:
    if not isinstance(value, list):
        _fail("목록이어야 한다.", got=type(value).__name__)
    options = [str(o) for o in config.get("options", [])]
    invalid = [v for v in value if not isinstance(v, str) or v not in options]
    if invalid:
        _fail("허용되지 않은 선택지가 있다.", options=options, invalid=invalid)
    # 중복은 조용히 제거한다. 순서는 유지 — 사용자가 고른 순서에 의미가 있다.
    seen: set[str] = set()
    unique: list[str] = []
    for item in value:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


@_register("user")
def _user(value: Any, _config: dict[str, Any]) -> str:
    """사용자 ID. 실존 여부는 서비스가 확인한다(여기서 DB 를 보지 않는다)."""
    return _as_uuid_string(value, "사용자 ID")


@_register("version")
def _version(value: Any, _config: dict[str, Any]) -> str:
    return _as_uuid_string(value, "버전 ID")


@_register("bool")
def _bool(value: Any, _config: dict[str, Any]) -> bool:
    if not isinstance(value, bool):
        _fail("true/false 여야 한다.", got=type(value).__name__)
    return value


@_register("url")
def _url(value: Any, _config: dict[str, Any]) -> str:
    if not isinstance(value, str):
        _fail("URL 문자열이어야 한다.", got=type(value).__name__)
    url = value.strip()
    # 스킴을 화이트리스트로 제한한다. javascript: 가 저장되면 렌더 시점에
    # 클릭 한 번으로 XSS 가 된다.
    if not url.startswith(("http://", "https://")):
        _fail("http 또는 https URL 이어야 한다.", value=url[:100])
    if len(url) > 2000:
        _fail("URL 이 너무 길다.", length=len(url))
    return url


def _as_uuid_string(value: Any, label: str) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        _fail(f"{label}여야 한다.", got=type(value).__name__)
    try:
        return str(UUID(value))
    except ValueError:
        _fail(f"{label}여야 한다.", value=value)
        raise  # pragma: no cover


def known_kinds() -> list[str]:
    return sorted(_validators)


def validate_value(kind: str, value: Any, config: dict[str, Any]) -> Any:
    """단일 값 검증. None 은 '비움'을 뜻하므로 그대로 통과시킨다."""
    if value is None:
        return None
    try:
        validator = _validators[kind]
    except KeyError as exc:
        raise ValidationError(
            f"알 수 없는 필드 종류: {kind!r}",
            code="issues.unknown_field_kind",
            details={"kind": kind, "known": known_kinds()},
        ) from exc
    return validator(value, config)
