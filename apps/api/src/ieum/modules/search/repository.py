"""색인 **쓰기**. 읽기는 `backends/` 가 한다.

읽는 쪽을 여기서 뗀 이유(ADR-0015): 읽기는 백엔드마다 다르고 쓰기는 아니다.
한 파일에 두면 "PGroonga 질의" 가 이 모듈의 성격처럼 보이는데, 이 모듈이
실제로 소유한 것은 **색인 한 줄의 모양**이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

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


__all__ = ["SearchRepository"]
