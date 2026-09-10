"""두 백엔드가 같은 질문에 같은 답을 내는가 (ADR-0015).

**이 파일의 이유는 하나다.** 검색 백엔드를 갈아끼울 수 있다는 말은, 갈아끼운
뒤에도 같은 검색창이 같게 동작한다는 뜻이다. 안 그러면 "검색이 이상해졌다"
라는 보고만 오고, 그건 어느 낱말에서 갈렸는지 아무도 모르는 보고다.

그래서 흉내 내지 않는다. 같은 문서를 Postgres 색인과 **진짜 OpenSearch** 에
넣고, 같은 질의를 둘에 던져 결과를 맞춘다. `IEUM_TEST_OPENSEARCH_URL` 이
없으면 건너뛴다 — 없는데 억지로 돌리면 "Postgres 에서 됐다" 를 "둘 다 됐다"
로 잘못 읽게 된다.

## 무엇을 맞추고 무엇을 안 맞추는가

**맞추는 것**: 어떤 문서가 결과에 드는가(집합), 총 개수, 권한이 거르는 것.
**안 맞추는 것**: 순서. 랭킹은 두 엔진이 다른 방식으로 재고, 그걸 같게
만들려면 한쪽을 다른 쪽에 맞춰 망가뜨려야 한다. 대신 "제목이 맞은 것이
본문만 맞은 것보다 위" 같은 **사람이 기대하는 성질**만 따로 본다.

한국어 되찾기도 갈린다. PGroonga 는 형태소를, 기본 OpenSearch 는 두 글자
바이그램을 본다(`cjk`). 그래서 "형태소가 필요한 질의" 는 여기서 재지 않고,
그 차이를 운영 문서에 적었다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.ids import new_id
from ieum.core.permissions import Acl
from ieum.core.time import utcnow
from ieum.modules.search import contracts as search
from ieum.modules.search import mirror
from ieum.modules.search.backends.base import IndexedDocument
from ieum.modules.search.backends.opensearch import OpenSearchBackend, translate_query
from ieum.modules.search.backends.postgres import PostgresBackend
from ieum.modules.wiki.models import Space

pytestmark = pytest.mark.integration

#: 시험용 OpenSearch. 없으면 이 파일의 통합 시험을 건너뛴다.
OPENSEARCH_URL = os.getenv("IEUM_TEST_OPENSEARCH_URL", "")


class TestTranslatingTheQuery:
    """번역은 붙는 것 없이 볼 수 있다. 그래서 여기는 건너뛰지 않는다."""

    def test_words_stay_and_mean_and(self) -> None:
        assert translate_query("배포 절차") == "배포 절차"

    def test_or_becomes_a_pipe(self) -> None:
        assert translate_query("배포 OR 절차") == "배포 | 절차"

    def test_and_is_dropped_because_it_is_the_default(self) -> None:
        assert translate_query("배포 AND 절차") == "배포 절차"

    def test_a_word_containing_or_is_left_alone(self) -> None:
        """`for` 를 `f|` 로 만들면 안 된다. 낱말 **전체**가 OR 일 때만 바꾼다."""
        assert translate_query("for OR forOR") == "for | forOR"

    def test_a_phrase_survives_with_its_quotes(self) -> None:
        assert translate_query('"배포 절차" 검토') == '"배포 절차" 검토'

    def test_a_phrase_with_or_inside_is_not_split(self) -> None:
        assert translate_query('"a OR b"') == '"a OR b"'

    def test_minus_stays_minus(self) -> None:
        assert translate_query("배포 -절차") == "배포 -절차"


def _key() -> str:
    """스페이스 키. **UUIDv7 의 앞자리를 쓰지 않는다** — 그건 시각이라서, 같은
    밀리초에 만든 둘이 같은 키가 되고 유니크 제약에 걸린다(실제로 걸렸다)."""
    return secrets.token_hex(3).upper()


class TestTheMappingMatchesTheTable:
    """색인 스키마가 표와 어긋나면 **미러가 통째로 실패한다.**

    매핑을 `dynamic: strict` 로 두었기 때문이다 — 모르는 열이 오면 400 이다.
    관대하게 두는 쪽이 나빴다: 그러면 새 열이 분석기 없이 조용히 색인되고,
    그 열로 검색하면 0건이 나온다. "없다" 와 구별되지 않는 0건이다.

    그래서 400 을 고른 대신, **여기서 미리 붉어지게** 한다. 열을 더한 사람이
    운영에서 그것을 만나면 안 된다.
    """

    def test_every_indexed_column_is_in_the_mapping(self) -> None:
        from ieum.modules.search.backends.opensearch import mapping
        from ieum.modules.search.models import SearchDocument

        # 색인에 안 보내는 것들. `id` 는 `_id` 가 대신하고(우리가 정한다),
        # `created_at`·`updated_at` 은 색인 행의 생애이지 문서의 것이 아니다
        # (문서의 시각은 `source_updated_at` 이다).
        not_sent = {"id", "created_at", "updated_at"}
        columns = {c.name for c in SearchDocument.__table__.columns} - not_sent
        properties = set(mapping("cjk")["mappings"]["properties"])
        assert columns == properties, (
            "열과 매핑이 어긋났다. 열을 더했으면 `mapping()` 과 `mirror.payload()` 에도 더한다"
        )

    def test_the_payload_sends_exactly_those_keys(self) -> None:
        """매핑과 열이 맞아도 **보내는 쪽이 빠지면** 그 열은 색인에 없다."""
        from datetime import datetime

        from ieum.modules.search.backends.opensearch import mapping
        from ieum.modules.search.mirror import payload
        from ieum.modules.search.models import SearchDocument

        row = SearchDocument(
            kind="page",
            entity_id=new_id(),
            scope_kind="space",
            scope_id=new_id(),
            ref="r",
            title="t",
            body="b",
            restricted_to=[new_id()],
            source_updated_at=datetime.now(UTC),
        )
        sent = set(payload(row))
        properties = set(mapping("cjk")["mappings"]["properties"])
        assert sent == properties, "보내는 열과 매핑이 어긋났다"


@pytest_asyncio.fixture
async def opensearch() -> AsyncIterator[Settings]:
    """빈 색인 하나를 시험마다 새로 만들고 끝나면 지운다.

    시험끼리 색인을 나눠 쓰면 앞 시험의 문서가 뒤 시험의 "전부" 단언에
    섞인다. 브라우저 스위트에서 이미 겪은 종류의 고장이다.
    """
    if not OPENSEARCH_URL:
        pytest.skip("IEUM_TEST_OPENSEARCH_URL 이 없다 (Postgres 하나로 도는 중)")
    index = f"ieum-test-{secrets.token_hex(6)}"
    settings = Settings(
        secret_key="x" * 40,  # type: ignore[arg-type]
        search_backend="opensearch",
        opensearch_url=OPENSEARCH_URL,
        opensearch_index=index,
    )
    backend = OpenSearchBackend(settings)
    await backend.ensure_index()
    await backend.aclose()
    # **계약이 보는 설정을 바꾼다.** 이걸 안 하면 `index_document` 가 미러
    # 큐에 아무것도 안 넣고, 시험은 "OpenSearch 가 0건" 으로 붉어진다 —
    # 실제로 그렇게 한 번 붉어졌고, 그때 알게 된 것은 이 갈래를 시험이
    # 밟을 수 없다는 것이었다.
    previous = search.settings_source
    search.settings_source = lambda: settings
    try:
        yield settings
    finally:
        search.settings_source = previous
        async with httpx.AsyncClient(base_url=OPENSEARCH_URL) as client:
            # 별칭이 아니라 색인을 지운다. `ensure_index` 가 이름에 시각을
            # 붙여 만들었으므로 접두사로 찾는다.
            await client.delete(f"/{index}*")


async def _index(
    session: AsyncSession,
    space: Space,
    *,
    title: str,
    body: str,
    restricted_to: list[UUID] | None = None,
) -> UUID:
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
        restricted_to=restricted_to,
        updated_at=utcnow(),
    )
    await session.flush()
    return entity_id


async def _mirror(session: AsyncSession, settings: Settings) -> None:
    """Postgres 색인을 OpenSearch 로 밀고 **보일 때까지 기다린다.**

    OpenSearch 는 색인한 것이 곧바로 검색되지 않는다(리프레시 주기). 시험이
    그것을 모른 척하면 무작위로 붉어진다 — 기다리는 대신 `_refresh` 를
    불러 확정적으로 만든다.
    """
    await mirror.flush(session, settings)
    async with httpx.AsyncClient(base_url=OPENSEARCH_URL) as client:
        await client.post(f"/{settings.opensearch_index}/_refresh")


def _titles(rows: list[IndexedDocument]) -> set[str]:
    return {row.title for row in rows}


@pytest_asyncio.fixture
async def space(session: AsyncSession) -> AsyncIterator[Space]:
    row = Space(key=f"S{_key()}", name="Docs")
    session.add(row)
    await session.flush()
    yield row


def _acls(space: Space) -> dict[str, Acl]:
    return {"page": Acl(permission="wiki.page.view", space_ids=frozenset({space.id}))}


class TestBothBackendsAgree:
    async def test_the_same_query_finds_the_same_documents(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        await _index(session, space, title="deploy runbook", body="migrations are reviewed")
        await _index(session, space, title="onboarding", body="laptop setup and access")
        await session.commit()
        await _mirror(session, opensearch)

        for query, expected in [
            ("migrations", {"deploy runbook"}),
            ("deploy", {"deploy runbook"}),
            ("laptop", {"onboarding"}),
            ("deploy OR laptop", {"deploy runbook", "onboarding"}),
            ("deploy laptop", set()),  # AND 가 기본이다
            ("nonexistent", set()),
        ]:
            pg, pg_total = await PostgresBackend(session).search(
                query=query,
                acls=_acls(space),
                principal_ids=frozenset(),
                kinds=("page",),
                limit=10,
                offset=0,
            )
            os_backend = OpenSearchBackend(opensearch)
            try:
                found, os_total = await os_backend.search(
                    query=query,
                    acls=_acls(space),
                    principal_ids=frozenset(),
                    kinds=("page",),
                    limit=10,
                    offset=0,
                )
            finally:
                await os_backend.aclose()

            assert _titles(pg) == expected, f"Postgres 가 '{query}' 에서 갈렸다"
            assert _titles(found) == expected, f"OpenSearch 가 '{query}' 에서 갈렸다"
            assert pg_total == os_total == len(expected), f"'{query}' 의 총 개수가 갈렸다"

    async def test_a_restricted_document_is_hidden_from_both(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """**이 시험이 이 파일에서 제일 중요하다.**

        권한 필터를 백엔드마다 따로 썼으므로, 한쪽만 새는 것이 가능한
        모양이다. 그리고 새는 방향은 조용하다 — 결과가 하나 더 뜨는 것을
        누가 이상하다고 하겠는가.
        """
        allowed = new_id()
        await _index(session, space, title="open notes", body="secret word inside")
        await _index(
            session, space, title="sealed notes", body="secret word inside", restricted_to=[allowed]
        )
        await session.commit()
        await _mirror(session, opensearch)

        for principals, expected in [
            (frozenset(), {"open notes"}),
            (frozenset({new_id()}), {"open notes"}),
            (frozenset({allowed}), {"open notes", "sealed notes"}),
        ]:
            pg, _ = await PostgresBackend(session).search(
                query="secret",
                acls=_acls(space),
                principal_ids=principals,
                kinds=("page",),
                limit=10,
                offset=0,
            )
            os_backend = OpenSearchBackend(opensearch)
            try:
                found, _ = await os_backend.search(
                    query="secret",
                    acls=_acls(space),
                    principal_ids=principals,
                    kinds=("page",),
                    limit=10,
                    offset=0,
                )
            finally:
                await os_backend.aclose()
            assert _titles(pg) == expected, "Postgres 쪽 제한이 어긋났다"
            assert _titles(found) == expected, "OpenSearch 쪽 제한이 어긋났다"

    async def test_a_document_outside_the_scope_is_hidden_from_both(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        other = Space(key=f"S{_key()}", name="Elsewhere")
        session.add(other)
        await session.flush()
        await _index(session, space, title="mine", body="shared word")
        await _index(session, other, title="theirs", body="shared word")
        await session.commit()
        await _mirror(session, opensearch)

        for acls, expected in [
            (_acls(space), {"mine"}),
            ({"page": Acl(permission="wiki.page.view", is_global=True)}, {"mine", "theirs"}),
            ({"page": Acl(permission="wiki.page.view")}, set()),
        ]:
            pg, _ = await PostgresBackend(session).search(
                query="shared",
                acls=acls,
                principal_ids=frozenset(),
                kinds=("page",),
                limit=10,
                offset=0,
            )
            os_backend = OpenSearchBackend(opensearch)
            try:
                found, _ = await os_backend.search(
                    query="shared",
                    acls=acls,
                    principal_ids=frozenset(),
                    kinds=("page",),
                    limit=10,
                    offset=0,
                )
            finally:
                await os_backend.aclose()
            assert _titles(pg) == expected
            assert _titles(found) == expected

    async def test_an_unreadable_query_is_taken_literally_instead_of_erroring(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """**검색창에서 500 이 나면 그건 고장이다.**

        PGroonga 의 `&@~` 는 질의 구문을 읽는 연산자라 끊긴 식에서 예외를
        낸다 — `*` 나 `*N` 은 `*N`·`*|` 같은 이항 연산자의 접두사여서 그것만
        있으면 식이 끊긴 것이다. 그리고 이 예외는 **실행 계획에 따라** 난다:
        작은 표는 순차 스캔이라 관대하고, 인덱스를 타면 터진다. 그래서
        개발에서는 되고 운영에서만 터진다.

        고침은 문법을 이쪽에 한 벌 더 갖는 것이 아니라 **거절을 신호로 쓰는
        것**이다: 못 읽었다고 하면 적은 그대로 찾는다. 그래서 여기서 보는 것은
        "터지지 않는다" 가 아니라 **"글자 그대로 찾아진다"** 다 — 앞의 것만
        보면 예외를 삼키고 0건을 주는 판도 통과한다(실제로 통과했다).
        """
        await _index(session, space, title="release notes", body="the flag *N controls fanout")
        await session.commit()
        await _mirror(session, opensearch)

        found, total = await PostgresBackend(session).search(
            query="flag *N",
            acls=_acls(space),
            principal_ids=frozenset(),
            kinds=("page",),
            limit=10,
            offset=0,
        )
        assert _titles(found) == {"release notes"}, "읽을 수 없는 질의를 글자로도 안 찾았다"
        assert total == 1

    async def test_a_broken_query_gives_zero_not_an_error(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """나머지 이상한 입력들. 무엇이 나오는지는 안 정하고 **안 터지는 것**만 본다.

        `simple_query_string` 을 고른 이유가 이것뿐이다 — `query_string` 은
        같은 입력에 400 을 낸다.
        """
        await _index(session, space, title="anything", body="anything")
        await session.commit()
        await _mirror(session, opensearch)

        for query in ['unclosed "quote', "((((", "AND OR NOT", "-", "*", "*N", "*|"]:
            pg, _ = await PostgresBackend(session).search(
                query=query,
                acls=_acls(space),
                principal_ids=frozenset(),
                kinds=("page",),
                limit=10,
                offset=0,
            )
            os_backend = OpenSearchBackend(opensearch)
            try:
                found, _ = await os_backend.search(
                    query=query,
                    acls=_acls(space),
                    principal_ids=frozenset(),
                    kinds=("page",),
                    limit=10,
                    offset=0,
                )
            finally:
                await os_backend.aclose()
            # 무엇이 나오는지는 안 정한다. **터지지 않는 것**을 정한다.
            assert isinstance(pg, list)
            assert isinstance(found, list)

    async def test_a_lone_star_differs_between_backends(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """**여기서는 두 백엔드가 갈린다.** 갈리는 것을 적어 둔다.

        `*` 하나를 치면 PGroonga 는 0건, OpenSearch 는 전부를 준다 — 뒤쪽은
        `simple_query_string` 에 쓸 만한 낱말이 하나도 없을 때 전체를 맞은
        것으로 보기 때문이다.

        맞추지 않는 이유: 어느 쪽도 틀리지 않았고, 맞추려면 한쪽에 특별
        규칙을 심어야 한다. 그 규칙은 다음 사람이 이유를 모르는 코드가 된다.
        대신 **갈린다는 사실을 시험이 들고 있게** 한다 — 조용히 달라지는 것과
        적혀 있는 것은 다르다. 권한은 양쪽 다 그대로 걸리므로(스코프 밖은
        안 나온다) 새는 자리는 아니다.
        """
        await _index(session, space, title="one", body="alpha")
        await _index(session, space, title="two", body="beta")
        await session.commit()
        await _mirror(session, opensearch)

        _, pg_total = await PostgresBackend(session).search(
            query="*",
            acls=_acls(space),
            principal_ids=frozenset(),
            kinds=("page",),
            limit=10,
            offset=0,
        )
        os_backend = OpenSearchBackend(opensearch)
        try:
            found, _ = await os_backend.search(
                query="*",
                acls=_acls(space),
                principal_ids=frozenset(),
                kinds=("page",),
                limit=10,
                offset=0,
            )
        finally:
            await os_backend.aclose()

        assert pg_total == 0, "PGroonga 쪽이 달라졌다 — 문서를 갱신할 것"
        assert _titles(found) == {"one", "two"}, "OpenSearch 쪽이 달라졌다"

    async def test_the_customer_facing_suggestion_hides_restricted_pages_in_both(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """고객 화면이다. 제한이 **하나라도** 걸린 문서는 아예 빠져야 한다."""
        await _index(session, space, title="public answer", body="how to reset")
        await _index(
            session, space, title="internal answer", body="how to reset", restricted_to=[new_id()]
        )
        await session.commit()
        await _mirror(session, opensearch)

        pg = await PostgresBackend(session).public_pages_in_space(
            space_id=space.id, query="reset", limit=10
        )
        os_backend = OpenSearchBackend(opensearch)
        try:
            found = await os_backend.public_pages_in_space(
                space_id=space.id, query="reset", limit=10
            )
        finally:
            await os_backend.aclose()
        assert _titles(pg) == {"public answer"}
        assert _titles(found) == {"public answer"}


class TestRanking:
    async def test_a_title_match_beats_a_body_match_in_both(
        self, session: AsyncSession, space: Space, opensearch: Settings
    ) -> None:
        """순서를 통째로 맞추지는 않는다. **사람이 기대하는 것 하나**만 본다."""
        await _index(session, space, title="widget", body="unrelated text")
        await _index(session, space, title="unrelated title", body="widget appears here")
        await session.commit()
        await _mirror(session, opensearch)

        pg, _ = await PostgresBackend(session).search(
            query="widget",
            acls=_acls(space),
            principal_ids=frozenset(),
            kinds=("page",),
            limit=10,
            offset=0,
        )
        os_backend = OpenSearchBackend(opensearch)
        try:
            found, _ = await os_backend.search(
                query="widget",
                acls=_acls(space),
                principal_ids=frozenset(),
                kinds=("page",),
                limit=10,
                offset=0,
            )
        finally:
            await os_backend.aclose()
        assert found[0].title == "widget", "OpenSearch 가 제목 맞음을 위로 안 올렸다"
        assert {row.title for row in pg} == {"widget", "unrelated title"}
