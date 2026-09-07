"""테스트 공용 픽스처.

DB 는 실제 Postgres 를 쓴다 (conventions.md 테스트 표).
`IEUM_TEST_DATABASE_URL` 이 있으면 그 DB 를, 없으면 testcontainers 로
일회용 컨테이너를 띄운다. CI 는 서비스 컨테이너를 쓰므로 전자를 탄다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.storage import ObjectStore
from ieum.db.models import Base

TEST_SECRET = "test-secret-key-at-least-32-characters-long-xxxx"

#: compose·CI 와 같은 이미지. PGroonga 가 들어 있다.
POSTGRES_IMAGE = "groonga/pgroonga:4.0.1-alpine-16"
#: 운영이 쓰는 확장 목록. 한 곳에서만 관리한다.
EXTENSIONS_SQL = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "compose"
    / "postgres-init"
    / "01-extensions.sql"
)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.getenv("IEUM_TEST_DATABASE_URL")
    if url:
        yield url
        return

    # 로컬에 DB 가 없을 때만 컨테이너를 띄운다. 느리므로 최후 수단이다.
    from testcontainers.postgres import PostgresContainer

    # compose·CI 와 **같은 이미지**를 쓴다. 여기만 맨 postgres 를 쓰면
    # PGroonga 를 쓰는 검색 테스트가 로컬에서만 조용히 실패한다(실제로 그랬다).
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
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
        # 확장은 마이그레이션이 아니라 초기화 SQL 이 깐다(운영과 같다).
        # 테스트 DB 는 그 SQL 을 안 거치므로 여기서 직접 깐다.
        for statement in EXTENSIONS_SQL.read_text(encoding="utf-8").splitlines():
            line = statement.strip()
            if line.startswith("CREATE EXTENSION"):
                await conn.execute(text(line.rstrip(";")))
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

    # 통합 테스트는 커밋을 하므로 끝나고도 데이터가 남는다. 치우지 않으면
    # 뒤에 도는 단위 테스트의 "전체 목록" 단언에 남의 행이 섞여 들어간다.
    async with factory() as s:
        await _truncate_all(s)
        await s.commit()


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


# ── 오브젝트 스토리지 ────────────────────────────────────────────
#
# 첨부를 쓰는 테스트가 여럿이라(첨부 자체, 위키 임포트) 여기 둔다.

_S3_ENDPOINT: list[str] = [""]


def s3_endpoint() -> str:
    """지금 도는 가짜 S3 의 주소. 버킷에 직접 쓰는 테스트가 쓴다."""
    return _S3_ENDPOINT[0]


@pytest.fixture(scope="session")
def s3_server() -> Iterator[str]:
    """in-process S3. 실제 HTTP 를 타므로 presigned URL 을 그대로 쓸 수 있다."""
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    _S3_ENDPOINT[0] = endpoint
    yield endpoint
    server.stop()


@pytest.fixture
def store(s3_server: str, settings: Settings) -> ObjectStore:
    configured = settings.model_copy(
        update={
            "s3_endpoint_url": s3_server,
            "s3_bucket": f"test-{secrets.token_hex(4)}",
            "s3_access_key": SecretStr("test"),
            "s3_secret_key": SecretStr("test"),
        }
    )
    return ObjectStore(configured)


@pytest_asyncio.fixture
async def ready_store(store: ObjectStore) -> AsyncIterator[ObjectStore]:
    await store.ensure_bucket()
    yield store
