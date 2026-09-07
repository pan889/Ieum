"""색인 읽기·쓰기. PGroonga 질의도 여기서 만든다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, and_, cast, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.permissions import Acl
from ieum.modules.search.models import SearchDocument

#: upsert 로 덮어쓰는 열. id·created_at 은 그대로 둔다.
_REFRESHED = (
    "scope_kind",
    "scope_id",
    "ref",
    "title",
    "body",
    "restricted_to",
    "source_updated_at",
)

#: PGroonga 랭킹 점수. 인덱스를 탈 때만 값이 나온다 — 작은 테이블은 순차
#: 스캔이라 전부 0 이다. 그래서 최근 순을 두 번째 기준으로 둔다.
_SCORE = text("pgroonga_score(search_document.tableoid, search_document.ctid) DESC")


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(self, values: dict[str, Any]) -> None:
        """같은 (kind, entity_id) 는 덮어쓴다.

        조회해 보고 없으면 넣는 식으로 하면, 같은 이슈를 동시에 두 번 저장할
        때 유니크 제약에 걸려 **저장 자체가** 실패한다. 색인 때문에 본업이
        실패하는 것은 있을 수 없다.
        """
        stmt = insert(SearchDocument).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[SearchDocument.kind, SearchDocument.entity_id],
                set_={k: stmt.excluded[k] for k in _REFRESHED},
            )
        )

    async def remove(self, kind: str, entity_id: UUID) -> None:
        await self._s.execute(
            delete(SearchDocument).where(
                SearchDocument.kind == kind, SearchDocument.entity_id == entity_id
            )
        )

    async def remove_many(self, kind: str, entity_ids: Sequence[UUID]) -> None:
        if not entity_ids:
            return
        await self._s.execute(
            delete(SearchDocument).where(
                SearchDocument.kind == kind, SearchDocument.entity_id.in_(list(entity_ids))
            )
        )

    async def search(
        self,
        *,
        query: str,
        acls: dict[str, Acl],
        principal_ids: frozenset[UUID],
        kinds: Sequence[str],
        limit: int,
        offset: int,
    ) -> tuple[list[SearchDocument], int]:
        """본문 검색 + 권한 필터. 총 개수도 함께 준다.

        권한을 SQL 로 내린다. 가져와서 거르면 페이지 크기가 어긋나고,
        무엇보다 한 군데라도 빠뜨리면 그게 유출이다.
        """
        scope = _scope_filter(acls, kinds)
        if scope is None:
            return [], 0

        # `&@~` 는 PGroonga 의 질의 구문 연산자다. 낱말을 띄어 쓰면 AND,
        # `OR` 로 나누면 OR. 문법이 깨져도 예외가 아니라 0건이 나온다.
        # 인자를 text 로 못 박는다. 안 하면 드라이버가 varchar 로 보내고
        # `text &@~ varchar` 연산자가 없다며 죽는다.
        needle = cast(query, Text)
        matched = or_(
            SearchDocument.title.bool_op("&@~")(needle),
            SearchDocument.body.bool_op("&@~")(needle),
        )
        restriction = or_(
            SearchDocument.restricted_to.is_(None),
            SearchDocument.restricted_to.overlap(list(principal_ids)),
        )
        where = and_(matched, scope, restriction)

        total = await self._s.scalar(select(func.count()).select_from(SearchDocument).where(where))
        rows = (
            await self._s.execute(
                select(SearchDocument)
                .where(where)
                .order_by(_SCORE, SearchDocument.source_updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return list(rows), int(total or 0)

    async def public_pages_in_space(
        self, *, space_id: UUID, query: str, limit: int
    ) -> list[SearchDocument]:
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
        needle = cast(query, Text)
        where = and_(
            SearchDocument.kind == "page",
            SearchDocument.scope_kind == "space",
            SearchDocument.scope_id == space_id,
            SearchDocument.restricted_to.is_(None),
            or_(
                SearchDocument.title.bool_op("&@~")(needle),
                SearchDocument.body.bool_op("&@~")(needle),
            ),
        )
        rows = (
            await self._s.execute(
                select(SearchDocument)
                .where(where)
                .order_by(_SCORE, SearchDocument.source_updated_at.desc())
                .limit(limit)
            )
        ).scalars()
        return list(rows)


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


__all__ = ["SearchRepository"]
