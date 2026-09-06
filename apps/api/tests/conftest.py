"""테스트 공용 픽스처.

DB 는 실제 Postgres 를 쓴다 (conventions.md 테스트 표).
`IEUM_TEST_DATABASE_URL` 이 있으면 그 DB 를, 없으면 testcontainers 로
일회용 컨테이너를 띄운다. CI 는 서비스 컨테이너를 쓰므로 전자를 탄다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.db.models import Base

TEST_SECRET = "test-secret-key-at-least-32-characters-long-xxxx"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.getenv("IEUM_TEST_DATABASE_URL")
    if url:
        yield url
        return

    # 로컬에 DB 가 없을 때만 컨테이너를 띄운다. 느리므로 최후 수단이다.
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        debug=True,
        secret_key=TEST_SECRET,  # type: ignore[arg-type]
        database_url=database_url,
        # 테스트에서 argon2 기본 파라미터(64MB)는 너무 느리다.
        argon2_memory_cost=8192,
        argon2_time_cost=1,
        argon2_parallelism=1,
    )


@pytest_asyncio.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[object]:
    eng = create_async_engine(settings.database_url, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: object) -> AsyncIterator[AsyncSession]:
    """테스트마다 롤백되는 세션.

    바깥 트랜잭션 안에서 돌리고 끝나면 롤백하므로 테스트 간 데이터가 새지 않는다.
    """
    conn = await engine.connect()  # type: ignore[attr-defined]
    trans = await conn.begin()
    factory = async_sessionmaker(bind=conn, expire_on_commit=False)
    async with factory() as s:
        yield s
    await trans.rollback()
    await conn.close()


@pytest_asyncio.fixture
async def app_client(engine: object, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """시드까지 끝난 실제 앱에 붙은 HTTP 클라이언트.

    통합 테스트는 라우터·의존성·권한 배선까지 함께 봐야 의미가 있다.
    DB 세션은 dependency_overrides 로 갈아끼운다 — 모듈 전역을 건드리면
    테스트끼리 상태가 샌다.
    """
    from ieum.config import get_settings
    from ieum.db.session import get_db_session
    from ieum.main import create_app
    from ieum.seed import seed

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]

    async with factory() as s:
        await _truncate_all(s)
        await seed(s, settings)
        await s.commit()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = _override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _truncate_all(session: AsyncSession) -> None:
    """테스트 간 격리. 통합 테스트는 커밋을 하므로 롤백으로는 부족하다."""
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def seed_admin() -> tuple[str, str]:
    return "admin@example.com", "seed-admin-password-1234"


@pytest.fixture
def actor() -> Actor:
    return Actor(user_id=new_id(), email="tester@example.com")


@pytest.fixture
def customer_actor() -> Actor:
    return Actor(user_id=new_id(), email="customer@example.com", is_customer=True)
