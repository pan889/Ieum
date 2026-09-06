"""요청 스코프 컨텍스트. trace_id 와 액터를 로깅·권한 계층에 전파한다."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_trace_id: ContextVar[str | None] = ContextVar("ieum_trace_id", default=None)


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def get_trace_id() -> str | None:
    return _trace_id.get()


@dataclass(slots=True)
class Actor:
    """요청을 수행하는 주체.

    권한 캐시는 요청 스코프로만 둔다. 요청을 넘는 캐시는 무효화 사고의 원인이다
    (docs/architecture/auth.md 5절).
    """

    user_id: UUID
    email: str
    is_customer: bool = False
    is_active: bool = True
    locale: str = "en"
    timezone: str = "UTC"
    session_id: UUID | None = None
    mfa_satisfied_at: Any = None
    group_ids: frozenset[UUID] = frozenset()

    # 요청 1회 동안만 유효한 권한 계산 캐시.
    _permission_cache: dict[Any, Any] = field(default_factory=dict, repr=False)

    @property
    def principal_ids(self) -> frozenset[UUID]:
        """역할 할당 조회에 쓸 주체 ID 집합 (본인 + 소속 그룹)."""
        return frozenset({self.user_id}) | self.group_ids

    async def cached(self, key: Any, factory: Callable[[], Awaitable[Any]]) -> Any:
        """요청 스코프 캐시. 같은 요청 안에서 같은 키는 1회만 계산한다."""
        if key not in self._permission_cache:
            self._permission_cache[key] = await factory()
        return self._permission_cache[key]
