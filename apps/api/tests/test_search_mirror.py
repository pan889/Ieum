"""Postgres 색인 → OpenSearch 미러 (ADR-0015).

미러가 하는 약속 넷을 본다. 넷 다 "안 지켜도 오류가 안 나는" 종류다 —
사본이 조용히 낡으면 사람은 "새 문서가 검색에 안 뜬다" 로만 만난다.

1. **색인을 쓰면 큐에 들어간다.** 이 배선이 빠지면 사본이 영원히 안 자란다.
2. **같은 문서를 여러 번 고쳐도 큐는 한 줄이다.** 뭉치는 것이 이 표를
   아웃박스 대신 둔 이유다.
3. **지움이 사본에서도 지워진다.** 남으면 열 수 없는 결과가 뜬다.
4. **보내기가 실패하면 큐에 남는다.** 먼저 지우면 그 문서는 다시 고쳐질
   때까지 영원히 사본에 없다.

`IEUM_TEST_OPENSEARCH_URL` 이 없으면 OpenSearch 가 필요한 것만 건너뛴다.
1·2·4 는 큐 쪽 성질이라 붙는 것 없이 볼 수 있다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.ids import new_id
from ieum.core.permissions import Acl
from ieum.core.time import utcnow
from ieum.modules.search import contracts as search
from ieum.modules.search import mirror
from ieum.modules.search.backends.opensearch import OpenSearchBackend
from ieum.modules.search.models import SearchMirrorQueue
from ieum.modules.wiki.models import Space

pytestmark = pytest.mark.integration

OPENSEARCH_URL = os.getenv("IEUM_TEST_OPENSEARCH_URL", "")


def _settings(index: str, *, url: str = "") -> Settings:
    return Settings(
        secret_key="x" * 40,  # type: ignore[arg-type]
        search_backend="opensearch",
        opensearch_url=url or OPENSEARCH_URL or "http://127.0.0.1:9",
        opensearch_index=index,
    )


@pytest_asyncio.fixture
async def mirroring(session: AsyncSession) -> AsyncIterator[Settings]:
    """계약이 미러 큐를 쓰게 만든다. OpenSearch 는 아직 안 건드린다."""
    settings = _settings(f"ieum-test-{secrets.token_hex(6)}")
    previous = search.settings_source
    search.settings_source = lambda: settings
    try:
        yield settings
    finally:
        search.settings_source = previous


@pytest_asyncio.fixture
async def space(session: AsyncSession) -> AsyncIterator[Space]:
    row = Space(key=f"S{secrets.token_hex(3).upper()}", name="Docs")
    session.add(row)
    await session.flush()
    yield row


async def _index(session: AsyncSession, space: Space, *, title: str, body: str = "b") -> UUID:
    entity_id = new_id()
    await search.index_document(
        session,
        kind=search.PAGE,
        entity_id=entity_id,
        scope_kind="space",
        scope_id=space.id,
        ref=f"{space.key}/{title}",
        title=title,
        body=body,
        restricted_to=None,
        updated_at=utcnow(),
    )
    await session.flush()
    return entity_id


async def _queued(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SearchMirrorQueue)) or 0)


class TestTheQueue:
    async def test_indexing_puts_the_key_in_the_queue(
        self, session: AsyncSession, space: Space, mirroring: Settings
    ) -> None:
        """**배선이 여기 있다.** 빠지면 사본이 영원히 안 자란다."""
        await _index(session, space, title="first")
        assert await _queued(session) == 1

    async def test_editing_the_same_document_twice_leaves_one_row(
        self, session: AsyncSession, space: Space, mirroring: Settings
    ) -> None:
        """뭉치는 것이 이 표를 아웃박스 대신 둔 이유다."""
        entity_id = await _index(session, space, title="same")
        for _ in range(5):
            await search.index_document(
                session,
                kind=search.PAGE,
                entity_id=entity_id,
                scope_kind="space",
                scope_id=space.id,
                ref="r",
                title="same",
                body="again",
                restricted_to=None,
                updated_at=utcnow(),
            )
        await session.flush()
        assert await _queued(session) == 1, "고칠 때마다 줄이 늘었다"

    async def test_removing_a_document_also_queues_it(
        self, session: AsyncSession, space: Space, mirroring: Settings
    ) -> None:
        """지움도 키가 큐에 들어가야 한다 — 사본에서 지울 사람이 있어야 한다."""
        entity_id = await _index(session, space, title="doomed")
        await session.execute(SearchMirrorQueue.__table__.delete())
        await search.remove_document(session, kind=search.PAGE, entity_id=entity_id)
        await session.flush()
        assert await _queued(session) == 1

    async def test_the_postgres_backend_queues_nothing(
        self, session: AsyncSession, space: Space
    ) -> None:
        """미러를 안 쓰는 설치는 이 표가 비어 있어야 한다.

        기본값이 그렇다는 것을 못 박는다 — 안 그러면 99% 의 설치가 색인을
        쓸 때마다 쓸데없는 INSERT 를 한 번씩 더 한다.
        """
        await _index(session, space, title="plain")
        assert await _queued(session) == 0

    async def test_a_failing_send_leaves_the_queue_alone(
        self, session: AsyncSession, space: Space, mirroring: Settings
    ) -> None:
        """**보낸 뒤에 지운다.** 먼저 지우면 실패한 키가 사라진다.

        닿지 않는 주소를 준다 — 흉내가 아니라 실제로 못 붙는 상황이다.
        """
        await _index(session, space, title="pending")
        await session.commit()
        broken = _settings(mirroring.opensearch_index, url="http://127.0.0.1:9")
        with pytest.raises(httpx.HTTPError):
            await mirror.flush(session, broken)
        # **되돌리지 않는다.** 시험 세션은 바깥 트랜잭션에 감싸여 있어서
        # 여기서 `rollback()` 을 부르면 방금 커밋한 줄까지 사라지고, 시험이
        # 제품 대신 자기 자신을 확인하게 된다(실제로 그렇게 붉어졌다).
        assert await _queued(session) == 1, "실패했는데 큐가 비었다"

    async def test_the_backlog_reports_what_is_waiting(
        self, session: AsyncSession, space: Space, mirroring: Settings
    ) -> None:
        """`/metrics` 가 읽는 값. 사람은 이것을 "새 문서가 안 뜬다" 로 만난다."""
        assert await mirror.backlog(session) == (0, 0.0)
        await _index(session, space, title="waiting")
        count, age = await mirror.backlog(session)
        assert count == 1
        assert age >= 0.0


@pytest_asyncio.fixture
async def live(mirroring: Settings) -> AsyncIterator[Settings]:
    """진짜 OpenSearch. 없으면 건너뛴다."""
    if not OPENSEARCH_URL:
        pytest.skip("IEUM_TEST_OPENSEARCH_URL 이 없다")
    backend = OpenSearchBackend(mirroring)
    await backend.ensure_index()
    await backend.aclose()
    try:
        yield mirroring
    finally:
        async with httpx.AsyncClient(base_url=OPENSEARCH_URL) as client:
            await client.delete(f"/{mirroring.opensearch_index}*")


async def _search(settings: Settings, query: str, space: Space) -> set[str]:
    async with httpx.AsyncClient(base_url=OPENSEARCH_URL) as client:
        await client.post(f"/{settings.opensearch_index}/_refresh")
    backend = OpenSearchBackend(settings)
    try:
        rows, _ = await backend.search(
            query=query,
            acls={"page": Acl(permission="wiki.page.view", space_ids=frozenset({space.id}))},
            principal_ids=frozenset(),
            kinds=("page",),
            limit=20,
            offset=0,
        )
    finally:
        await backend.aclose()
    return {row.title for row in rows}


class TestFlushing:
    async def test_what_is_indexed_becomes_searchable_and_the_queue_empties(
        self, session: AsyncSession, space: Space, live: Settings
    ) -> None:
        await _index(session, space, title="mirrored", body="findable text")
        await session.commit()
        assert await mirror.flush(session, live) == 1
        await session.commit()
        assert await _queued(session) == 0
        assert await _search(live, "findable", space) == {"mirrored"}

    async def test_a_removed_document_disappears_from_the_copy(
        self, session: AsyncSession, space: Space, live: Settings
    ) -> None:
        """**남으면 열 수 없는 결과가 뜬다.** 사본에서도 지워져야 한다."""
        entity_id = await _index(session, space, title="temporary", body="ghost text")
        await session.commit()
        await mirror.flush(session, live)
        await session.commit()
        assert await _search(live, "ghost", space) == {"temporary"}

        await search.remove_document(session, kind=search.PAGE, entity_id=entity_id)
        await session.commit()
        await mirror.flush(session, live)
        await session.commit()
        assert await _search(live, "ghost", space) == set(), "지운 문서가 사본에 남았다"

    async def test_the_flush_sends_the_current_state_not_the_queued_one(
        self, session: AsyncSession, space: Space, live: Settings
    ) -> None:
        """큐에 값이 없는 이유다. 보낼 때 **다시 읽어서** 지금 상태를 보낸다.

        그래서 순서를 안 따진다 — 한 번에 스무 번 고쳐도 마지막 것이 간다.
        """
        entity_id = await _index(session, space, title="draft", body="first wording")
        await search.index_document(
            session,
            kind=search.PAGE,
            entity_id=entity_id,
            scope_kind="space",
            scope_id=space.id,
            ref="r",
            title="draft",
            body="second wording",
            restricted_to=None,
            updated_at=utcnow(),
        )
        await session.commit()
        await mirror.flush(session, live)
        await session.commit()
        assert await _search(live, "second", space) == {"draft"}
        assert await _search(live, "first", space) == set(), "옛 본문이 사본에 갔다"


class TestRebuilding:
    async def test_reindex_refills_the_copy_and_keeps_search_working(
        self, session: AsyncSession, space: Space, live: Settings
    ) -> None:
        """**켠 직후에 한 번 돌려야 하는 명령이다.** 켜기 전의 것은 큐에 없다.

        그리고 담는 동안 검색이 0건이 되지 않아야 한다 — 별칭을 나중에 옮기는
        이유다. 여기서는 담은 뒤 같은 별칭으로 찾아지는 것까지 본다.
        """
        from ieum.reindex import mirror_all

        # 큐를 비워 "켜기 전에 쌓인 데이터" 를 만든다.
        await _index(session, space, title="older", body="legacy text")
        await session.execute(SearchMirrorQueue.__table__.delete())
        await session.commit()
        assert await _search(live, "legacy", space) == set()

        sent = await mirror_all(session, live)
        await session.commit()
        assert sent >= 1
        assert await _search(live, "legacy", space) == {"older"}
        assert await _queued(session) == 0

    async def test_rebuilding_twice_leaves_one_index_behind_the_alias(
        self, session: AsyncSession, space: Space, live: Settings
    ) -> None:
        """옛 색인을 안 지우면 디스크가 되색인 횟수만큼 늘어난다."""
        from ieum.reindex import mirror_all

        await _index(session, space, title="kept", body="stays around")
        await session.commit()
        await mirror_all(session, live)
        await mirror_all(session, live)
        await session.commit()

        async with httpx.AsyncClient(base_url=OPENSEARCH_URL) as client:
            found: dict[str, Any] = (await client.get(f"/_alias/{live.opensearch_index}")).json()
        assert len(found) == 1, f"별칭에 색인이 {len(found)}개 붙어 있다"
        assert await _search(live, "stays", space) == {"kept"}
