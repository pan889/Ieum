"""도메인 이벤트와 구독 레지스트리.

이벤트는 **트랜잭션 커밋 이후에만** 발행된다. 커밋 전에 발행하면 롤백된
변경에 대한 알림이 나가는 유령 알림이 생긴다 (docs/architecture/overview.md).
그래서 발행은 곧 아웃박스 테이블에 같은 트랜잭션으로 기록하는 것이고,
실제 디스패치는 워커가 폴링해서 한다.

페이로드에 ORM 객체를 넣지 않는다. ID + 최소 스냅샷만 담는다
(module-guide 모듈 간 통신 2번).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, ClassVar, TypeVar
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    """모든 도메인 이벤트의 뿌리."""

    #: 아웃박스에 저장되는 안정 식별자. 바꾸면 미처리 이벤트가 유실된다.
    event_type: ClassVar[str] = ""
    #: 이벤트가 속한 애그리게이트 종류 (issue, page, user ...)
    aggregate_type: ClassVar[str] = ""

    aggregate_id: UUID

    def payload(self) -> dict[str, Any]:
        """JSON 직렬화 가능한 페이로드."""
        assert is_dataclass(self)
        return {k: _jsonable(v) for k, v in asdict(self).items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, frozenset | set):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


E = TypeVar("E", bound=DomainEvent)
Handler = Callable[[DomainEvent], Awaitable[None]]


class EventRegistry:
    """이벤트 타입 ↔ 클래스, 그리고 구독자 목록."""

    def __init__(self) -> None:
        self._types: dict[str, type[DomainEvent]] = {}
        self._handlers: dict[str, list[Handler]] = {}

    def register_event(self, event_cls: type[E]) -> type[E]:
        """이벤트 클래스를 등록한다. 데코레이터로도 쓴다."""
        key = event_cls.event_type
        if not key:
            raise ValueError(f"{event_cls.__name__} 에 event_type 이 없다.")
        existing = self._types.get(key)
        if existing is not None and existing is not event_cls:
            raise ValueError(f"event_type '{key}' 가 이미 {existing.__name__} 에 등록됐다.")
        self._types[key] = event_cls
        return event_cls

    def subscribe(self, event_cls: type[DomainEvent]) -> Callable[[Handler], Handler]:
        """핸들러 등록 데코레이터. 구독은 자기 모듈 안에서만 한다."""

        def decorator(handler: Handler) -> Handler:
            self._handlers.setdefault(event_cls.event_type, []).append(handler)
            return handler

        return decorator

    def handlers_for(self, event_type: str) -> list[Handler]:
        return list(self._handlers.get(event_type, ()))

    def event_class(self, event_type: str) -> type[DomainEvent] | None:
        return self._types.get(event_type)

    def known_event_types(self) -> frozenset[str]:
        return frozenset(self._types)


events = EventRegistry()
