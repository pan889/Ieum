"""비동기 세션 관리.

`conventions.md` 는 트랜잭션 경계를 **서비스 메서드**로 정해 두었다. 그런데
이 저장소의 실제 관행은 **라우터 커밋**이다 — 쓰기 라우트 118개 중 113개가
끝에서 `await session.commit()` 을 부른다(나머지 5개는 저장할 것이 없는
라우트다). 한동안 이 주석이 반대를 말하고 있었고, 그 사이 desk 라우터가
커밋을 통째로 빠뜨린 채 201 을 돌려주었다.

그래서 여기 있는 문장을 **현실에 맞춘다.** 규칙을 바꾼 것이 아니라, 코드가
문서와 어긋나 있다는 사실을 코드 쪽에도 적어 둔 것이다(어긋남 자체는 문서
저장소에 별도 변경으로 올렸다). 지금 지켜야 할 것:

- 쓰기 라우트는 끝에서 커밋한다. `test_router_commits.py` 가 정적으로 막고,
  `test_desk_api.py` 는 쓴 뒤 **다른 요청**으로 읽어 실제로 남았는지 본다.
- 여기서 요청 끝에 자동 커밋을 넣지 않는다. 넣으면 "일부러 커밋하지 않는"
  라우트까지 커밋되고, 그 변화는 조용하다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ieum.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def init_engine(settings: Settings) -> AsyncEngine:
    """프로세스 시작 시 1회 호출."""
    global _engine, _session_factory
    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("init_engine() 을 먼저 호출해야 한다.")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """워커·CLI 용 세션 스코프. 예외 시 롤백한다."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성. 커밋은 서비스가 한다."""
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
