"""notify 의 공개 인터페이스.

다른 모듈이 구독 여부를 묻거나, 보낼 메일 한 통을 만들어 넘길 때 쓴다.
메일 **발송**은 여기서 하지 않는다 — 워커가 트랜잭션 밖에서 보낸다.
SMTP 가 느리면 커넥션을 붙잡고 락이 쌓인다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.notify.mail import Mail
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


__all__ = ["Mail", "is_watching", "watch", "watchers_of"]
