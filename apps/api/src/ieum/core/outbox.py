"""트랜잭션 안전 이벤트 발행 (아웃박스 패턴).

서비스는 도메인 변경과 **같은 트랜잭션**에서 `publish()` 를 호출한다.
행이 커밋돼야 이벤트도 존재하므로 "DB 는 바뀌었는데 알림이 안 갔다"거나
"알림은 갔는데 롤백됐다"가 구조적으로 불가능하다.

워커가 `published_at IS NULL` 인 행을 폴링해 디스패치한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ieum.core.events import DomainEvent, EventEnvelope, events
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.db.base import Entity

log = get_logger(__name__)

MAX_ATTEMPTS = 10


class OutboxEvent(Entity):
    """미발행 도메인 이벤트."""

    __tablename__ = "outbox_event"

    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (
        # 워커 폴링 경로. 미발행 행만 대상이므로 부분 인덱스로 작게 유지한다.
        Index(
            "ix_outbox_event_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index("ix_outbox_event_aggregate", "aggregate_type", "aggregate_id"),
    )


async def outbox_backlog(session: AsyncSession) -> tuple[int, float]:
    """아직 안 나간 이벤트 수와, 그중 가장 오래된 것의 나이(초).

    **개수보다 나이가 말해 준다.** 백 건이 방금 들어온 것은 정상이고, 한
    건이 열 분째 남아 있는 것은 워커가 죽었거나 그 한 건이 계속 실패하는
    것이다. 둘을 개수로만 보면 구별되지 않는다.

    재시도 상한을 넘긴 행도 센다. 그것들은 영원히 안 나가므로, 안 세면
    "밀린 것 없음" 인 채로 조용히 쌓인다.
    """
    row = (
        await session.execute(
            select(
                func.count(OutboxEvent.id),
                func.min(OutboxEvent.created_at),
            ).where(OutboxEvent.published_at.is_(None))
        )
    ).one()
    pending = int(row[0] or 0)
    oldest: datetime | None = row[1]
    return pending, (utcnow() - oldest).total_seconds() if oldest is not None else 0.0


def publish(session: AsyncSession, event: DomainEvent) -> OutboxEvent:
    """이벤트를 현재 트랜잭션에 실어 보낸다. 커밋은 호출자(서비스)가 한다."""
    if not event.event_type:
        raise ValueError(f"{type(event).__name__} 에 event_type 이 없다.")
    row = OutboxEvent(
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        event_type=event.event_type,
        payload=event.payload(),
    )
    session.add(row)
    return row


async def fetch_unpublished(session: AsyncSession, *, limit: int = 100) -> list[OutboxEvent]:
    """미발행 이벤트를 오래된 순으로 잠근 채 가져온다.

    `SKIP LOCKED` 로 워커를 여러 개 띄워도 같은 행을 두 번 처리하지 않는다.
    """
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .where(OutboxEvent.attempts < MAX_ATTEMPTS)
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def dispatch(session: AsyncSession, row: OutboxEvent) -> None:
    """한 건을 구독자에게 전달하고 결과를 기록한다.

    구독자가 없는 이벤트도 발행 처리한다. 아무도 안 듣는다고 아웃박스에
    쌓아두면 폴링이 매번 같은 행을 다시 집는다.
    """
    envelope = EventEnvelope(
        id=row.id,
        event_type=row.event_type,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        payload=dict(row.payload),
    )
    handlers = events.handlers_for(row.event_type)

    try:
        for handler in handlers:
            await handler(envelope)
    # 한 건이 실패해도 루프 전체를 멈추지 않는다. 실패는 행에 기록하고 재시도한다.
    except Exception as exc:
        row.attempts += 1
        row.last_error = f"{type(exc).__name__}: {exc}"
        log.error(
            "outbox.dispatch_failed",
            event_type=row.event_type,
            id=str(row.id),
            attempts=row.attempts,
            error=row.last_error,
        )
        return

    row.published_at = utcnow()
    log.info("outbox.dispatched", event_type=row.event_type, handlers=len(handlers))
