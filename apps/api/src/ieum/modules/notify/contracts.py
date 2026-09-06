"""notify 의 공개 인터페이스. 다른 모듈이 구독 여부를 물을 때 쓴다."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.notify.repository import WatchRepository


async def watchers_of(session: AsyncSession, target_type: str, target_id: UUID) -> frozenset[UUID]:
    return frozenset(await WatchRepository(session).watchers_of(target_type, target_id))


async def is_watching(
    session: AsyncSession, user_id: UUID, target_type: str, target_id: UUID
) -> bool:
    return await WatchRepository(session).is_watching(user_id, target_type, target_id)


async def watch(session: AsyncSession, user_id: UUID, target_type: str, target_id: UUID) -> bool:
    """다른 모듈이 자동 구독을 걸 때. 예: 이슈를 만들면 보고자를 구독시킨다."""
    return await WatchRepository(session).add(user_id, target_type, target_id)
