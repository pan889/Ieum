"""imports 쿼리. 비즈니스 로직 없음."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.imports.models import ImportedObject


class ImportedObjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def known_source_ids(
        self, *, project_id: UUID, source_kind: str, target_type: str, source_ids: Iterable[str]
    ) -> set[str]:
        """이 중 이미 옮긴 것들의 소스 id.

        **한 번에 묻는다.** 이슈마다 한 번씩 물으면 5만 개짜리 묶음에서
        왕복이 5만 번이다.
        """
        wanted = list(source_ids)
        if not wanted:
            return set()
        rows = await self._s.execute(
            select(ImportedObject.source_id).where(
                ImportedObject.project_id == project_id,
                ImportedObject.source_kind == source_kind,
                ImportedObject.target_type == target_type,
                ImportedObject.source_id.in_(wanted),
            )
        )
        return set(rows.scalars())

    async def resolve(
        self, *, project_id: UUID, source_kind: str, target_type: str, source_ids: Iterable[str]
    ) -> dict[str, UUID]:
        """소스 id → 우리 id. 부모와 관계를 잇는 데 쓴다."""
        wanted = list(source_ids)
        if not wanted:
            return {}
        rows = await self._s.execute(
            select(ImportedObject.source_id, ImportedObject.target_id).where(
                ImportedObject.project_id == project_id,
                ImportedObject.source_kind == source_kind,
                ImportedObject.target_type == target_type,
                ImportedObject.source_id.in_(wanted),
            )
        )
        return {row.source_id: row.target_id for row in rows.all()}

    def add(self, row: ImportedObject) -> ImportedObject:
        self._s.add(row)
        return row


__all__ = ["ImportedObjectRepository"]
