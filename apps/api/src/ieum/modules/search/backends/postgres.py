"""PGroonga 백엔드 — 기본값 (ADR-0005).

색인이 그냥 Postgres 의 한 표이므로, 이 백엔드는 **원본과 같은 트랜잭션에서
읽는다.** 방금 저장한 것이 곧바로 검색되는 성질이 여기서 나온다 — 다른
백엔드는 그럴 수 없고, 그것이 기본값을 여기 두는 이유다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Text, and_, cast, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.logging import get_logger
from ieum.core.permissions import Acl
from ieum.modules.search.backends.base import IndexedDocument
from ieum.modules.search.models import SearchDocument

#: PGroonga 랭킹 점수. 인덱스를 탈 때만 값이 나온다 — 작은 테이블은 순차
#: 스캔이라 전부 0 이다. 그래서 최근 순을 두 번째 기준으로 둔다.
logger = get_logger(__name__)

_SCORE = text("pgroonga_score(search_document.tableoid, search_document.ctid) DESC")


class PostgresBackend:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def search(
        self,
        *,
        query: str,
        acls: dict[str, Acl],
        principal_ids: frozenset[UUID],
        kinds: Sequence[str],
        limit: int,
        offset: int,
    ) -> tuple[list[IndexedDocument], int]:
        """본문 검색 + 권한 필터. 총 개수도 함께 준다.

        권한을 SQL 로 내린다. 가져와서 거르면 페이지 크기가 어긋나고,
        무엇보다 한 군데라도 빠뜨리면 그게 유출이다.
        """
        scope = _scope_filter(acls, kinds)
        if scope is None:
            return [], 0

        def build(matched: ColumnElement[bool]) -> ColumnElement[bool]:
            return and_(matched, scope, _restriction(principal_ids))

        return await self._run(query, build, limit=limit, offset=offset)

    async def _run(
        self,
        query: str,
        build: Callable[[ColumnElement[bool]], ColumnElement[bool]],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[IndexedDocument], int]:
        """질의 구문으로 한 번 해 보고, **PGroonga 가 못 읽으면 글자 그대로** 찾는다.

        `&@~` 는 구문을 읽는 연산자라 불완전한 식에서 오류를 낸다 — 검색창에
        `*` 하나를 치면 그것이다(`*` 는 `*N`·`*|` 같은 이항 연산자의
        접두사여서, 그것만 있으면 PGroonga 에게는 끊긴 식이다). 사람이 아무거나
        치는 자리에서 500 은 답이 아니다.

        구문을 흉내 내서 미리 걸러 내지 않는다. 그건 PGroonga 의 문법을 이쪽에
        한 벌 더 갖는 일이고, 버전이 올라가면 두 벌이 어긋난다. 대신 **거절을
        신호로 쓴다**: 못 읽었다고 하면 `&@`(그냥 낱말 포함)로 다시 묻는다.
        "연산자를 못 읽었으니 적은 그대로 찾았다" 는 0건보다 낫다.

        세이브포인트로 감싸는 이유: Postgres 는 오류가 나면 트랜잭션을
        무르므로, 되묻기 전에 그 지점으로 돌아가야 한다.
        """
        for matched in (_matched(query), _literal(query)):
            where = build(matched)
            try:
                async with self._s.begin_nested():
                    total = await self._s.scalar(
                        select(func.count()).select_from(SearchDocument).where(where)
                    )
                    rows = list(
                        (
                            await self._s.execute(
                                select(SearchDocument)
                                .where(where)
                                .order_by(_SCORE, SearchDocument.source_updated_at.desc())
                                .limit(limit)
                                .offset(offset)
                            )
                        )
                        .scalars()
                        .all()
                    )
            except DBAPIError as exc:
                if not _unreadable_query(exc):
                    raise
                logger.info("search.query_unreadable", query=query[:200])
                continue
            return [_document(row) for row in rows], int(total or 0)
        # 두 번째(글자 그대로)는 구문을 안 읽으므로 여기 닿지 않는다.
        return [], 0  # pragma: no cover

    async def public_pages_in_space(
        self, *, space_id: UUID, query: str, limit: int
    ) -> list[IndexedDocument]:
        """스페이스 하나에서 **아무 제한도 없는** 문서만 찾는다 (desk C8).

        고객에게 보여 줄 것이라 권한(Acl)을 받지 않는다. 대신 조건을 좁게
        고정한다:

        - `restricted_to IS NULL` — 제한이 걸린 문서는 **한 명이라도** 볼 수
          있는 주체가 지정된 것이고, 고객은 그 목록에 없다. `overlap` 으로
          거르지 않고 아예 뺀다: 고객에게는 "제한이 없는 것" 만 보여 준다.
        - 초안은 애초에 색인에 없다(`wiki/service.py` 의 `_reindex`).

        스페이스가 고객에게 보여도 되는 것인지는 **부르는 쪽이** 판단한다 —
        `desk` 가 `kind = "kb"` 인 스페이스만 걸 수 있게 한다.
        """

        def build(matched: ColumnElement[bool]) -> ColumnElement[bool]:
            return and_(
                SearchDocument.kind == "page",
                SearchDocument.scope_kind == "space",
                SearchDocument.scope_id == space_id,
                SearchDocument.restricted_to.is_(None),
                matched,
            )

        # 고객이 치는 검색창이기도 하다. 읽을 수 없는 질의에 500 을 주면
        # 그건 포털이 고장 난 것으로 보인다.
        rows, _ = await self._run(query, build, limit=limit, offset=0)
        return rows

    async def aclose(self) -> None:
        """세션은 요청이 갖고 있다. 여기서 닫을 것이 없다."""
        return None


def _literal(query: str) -> ColumnElement[bool]:
    """`&@` — 구문을 읽지 않고 **낱말 포함**만 본다. 되묻기용이다."""
    needle = cast(query, Text)
    return or_(
        SearchDocument.title.bool_op("&@")(needle),
        SearchDocument.body.bool_op("&@")(needle),
    )


#: PGroonga 가 질의를 못 읽었을 때 남기는 말. 이 문구로만 되묻는다 —
#: 아무 DB 오류에나 되묻으면 진짜 고장을 "결과 0건" 으로 덮는다.
_UNREADABLE = "failed to parse expression"


def _unreadable_query(error: DBAPIError) -> bool:
    return _UNREADABLE in str(error.orig)


def _matched(query: str) -> ColumnElement[bool]:
    """`&@~` 는 PGroonga 의 질의 구문 연산자다.

    낱말을 띄어 쓰면 AND, `OR` 로 나누면 OR. 문법이 깨져도 예외가 아니라
    0건이 나온다 — 사람이 검색창에 아무거나 치는 자리이므로 그게 맞다.

    인자를 text 로 못 박는다. 안 하면 드라이버가 varchar 로 보내고
    `text &@~ varchar` 연산자가 없다며 죽는다.
    """
    needle = cast(query, Text)
    return or_(
        SearchDocument.title.bool_op("&@~")(needle),
        SearchDocument.body.bool_op("&@~")(needle),
    )


def _restriction(principal_ids: frozenset[UUID]) -> ColumnElement[bool]:
    return or_(
        SearchDocument.restricted_to.is_(None),
        SearchDocument.restricted_to.overlap(list(principal_ids)),
    )


def _scope_filter(acls: dict[str, Acl], kinds: Sequence[str]) -> ColumnElement[bool] | None:
    """종류마다 다른 권한을 쓴다. 아무 데도 권한이 없으면 None."""
    clauses: list[ColumnElement[bool]] = []
    for kind in kinds:
        acl = acls.get(kind)
        if acl is None or acl.is_empty:
            continue
        if acl.is_global:
            clauses.append(SearchDocument.kind == kind)
            continue
        ids = acl.project_ids if kind == "issue" else acl.space_ids
        if not ids:
            continue
        clauses.append(and_(SearchDocument.kind == kind, SearchDocument.scope_id.in_(list(ids))))
    if not clauses:
        return None
    return or_(*clauses)


def _document(row: SearchDocument) -> IndexedDocument:
    return IndexedDocument(
        kind=row.kind,
        entity_id=row.entity_id,
        scope_id=row.scope_id,
        ref=row.ref,
        title=row.title,
        body=row.body,
        source_updated_at=row.source_updated_at,
    )


__all__ = ["PostgresBackend"]
