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

from sqlalchemy import DateTime, Index, Integer, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ieum.core.events import DomainEvent, events
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
    """한 건을 구독자에게 전달하고 결과를 기록한다."""
    handlers = events.handlers_for(row.event_type)
    event_cls = events.event_class(row.event_type)

    if event_cls is None:
        # 코드에서 사라진 이벤트 타입. 재시도해도 소용없으므로 발행 처리하고 남긴다.
        log.warning("outbox.unknown_event_type", event_type=row.event_type, id=str(row.id))
        row.published_at = utcnow()
        return

    try:
        event = event_cls(**_rehydrate(event_cls, row.payload))
        for handler in handlers:
            await handler(event)
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


def _rehydrate(event_cls: type[DomainEvent], payload: dict[str, Any]) -> dict[str, Any]:
    """JSON 페이로드를 이벤트 생성자 인자로 되돌린다 (UUID 필드 복원)."""
    import dataclasses

    restored: dict[str, Any] = {}
    for f in dataclasses.fields(event_cls):
        if f.name not in payload:
            continue
        value = payload[f.name]
        annotation = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
        if "UUID" in annotation and isinstance(value, str):
            restored[f.name] = UUID(value)
        else:
            restored[f.name] = value
    return restored
