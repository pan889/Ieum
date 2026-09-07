"""워커 작업.

파이프라인은 명시적이다:

    아웃박스 폴링(SKIP LOCKED)
      → 알림 행 생성 + 웹훅 전송 행 생성
      → published_at 기록 → 커밋
      → (트랜잭션 밖) 메일 발송, 웹훅 HTTP 전송

발송을 트랜잭션 안에서 하지 않는다. SMTP 나 느린 엔드포인트 하나가
DB 트랜잭션을 붙잡고 있으면 락이 쌓인다.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from ieum.config import get_settings
from ieum.core.attachments import AttachmentService
from ieum.core.crypto import SecretBox
from ieum.core.logging import get_logger
from ieum.core.outbox import dispatch, fetch_unpublished
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.db.session import session_scope
from ieum.modules.notify import delivery as webhook_delivery
from ieum.modules.notify.handlers import (
    HandlerContext,
    enqueue_webhooks,
    handle_issue_event,
    handle_page_event,
)
from ieum.modules.notify.mail import send_all
from ieum.modules.notify.models import Notification, Webhook
from ieum.modules.notify.repository import DeliveryRepository
from ieum.modules.notify.service import NotificationService

log = get_logger(__name__)

OUTBOX_BATCH = 100
WEBHOOK_BATCH = 50


async def drain_outbox() -> int:
    """미발행 이벤트를 처리한다. 처리한 건수를 돌려준다."""
    settings = get_settings()
    notification_ids: list[Any] = []

    async with session_scope() as session:
        rows = await fetch_unpublished(session, limit=OUTBOX_BATCH)
        if not rows:
            return 0

        handler_ctx = HandlerContext(session=session, settings=settings)
        for row in rows:
            from ieum.core.events import EventEnvelope

            envelope = EventEnvelope(
                id=row.id,
                event_type=row.event_type,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                payload=dict(row.payload),
            )
            try:
                notification_ids.extend(await handle_issue_event(handler_ctx, envelope))
                notification_ids.extend(await handle_page_event(handler_ctx, envelope))
                await enqueue_webhooks(handler_ctx, envelope)
            # 한 건이 실패해도 배치 전체를 멈추지 않는다.
            except Exception as exc:
                row.attempts += 1
                row.last_error = f"{type(exc).__name__}: {exc}"
                log.error(
                    "outbox.handler_failed",
                    event_type=row.event_type,
                    id=str(row.id),
                    error=row.last_error,
                )
                continue

            # 모듈이 스스로 등록한 구독자도 돌린다.
            await dispatch(session, row)

        await session.flush()

    log.info("outbox.batch", processed=len(rows), notifications=len(notification_ids))

    if notification_ids:
        await send_pending_mail(notification_ids)
    return len(rows)


async def send_pending_mail(notification_ids: list[Any]) -> int:
    """알림에 대응하는 메일을 보낸다. 트랜잭션 밖에서 돈다."""
    settings = get_settings()
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(Notification).where(Notification.id.in_(notification_ids))
                )
            )
            .scalars()
            .all()
        )
        service = NotificationService(session, settings)
        mails = await service.pending_mail(rows)

    if not mails:
        return 0
    # 세션을 닫은 뒤 보낸다. SMTP 가 느려도 DB 커넥션을 붙잡지 않는다.
    sent = await send_all(settings, mails)
    log.info("mail.batch", queued=len(mails), sent=sent)
    return sent


async def deliver_webhooks() -> int:
    """전송할 차례가 된 웹훅을 보낸다."""
    settings = get_settings()
    box = SecretBox(settings.secret_key.get_secret_value(), purpose="notify.webhook")

    async with session_scope() as session:
        deliveries = await DeliveryRepository(session).due(limit=WEBHOOK_BATCH)
        if not deliveries:
            return 0

        webhook_ids = {d.webhook_id for d in deliveries}
        webhooks = {
            w.id: w
            for w in (await session.execute(select(Webhook).where(Webhook.id.in_(webhook_ids))))
            .scalars()
            .all()
        }

        async with httpx.AsyncClient(follow_redirects=False) as client:
            for row in deliveries:
                webhook = webhooks.get(row.webhook_id)
                if webhook is None:
                    row.status = "abandoned"
                    row.error = "웹훅이 삭제됨"
                    continue
                if not webhook.enabled:
                    # 꺼진 웹훅은 재시도하지 않는다. 다시 켜면 새 이벤트부터 간다.
                    row.status = "abandoned"
                    row.error = "웹훅이 비활성 상태"
                    continue

                try:
                    secret = box.decrypt(webhook.secret_enc)
                # 시크릿을 못 푸는 웹훅 하나가 배치 전체를 멈추면 안 된다.
                # 키 로테이션이나 다른 키로 만든 DB 를 복원하면 실제로 생긴다.
                except Exception as exc:
                    row.status = "abandoned"
                    row.error = f"시크릿 복호화 실패: {type(exc).__name__}"
                    webhook.enabled = False
                    webhook.disabled_reason = "시크릿을 복호화할 수 없음"
                    log.error(
                        "webhook.secret_undecryptable",
                        webhook_id=str(webhook.id),
                        error=str(exc)[:200],
                    )
                    continue

                outcome = await webhook_delivery.post(webhook, row, secret, client=client)
                webhook_delivery.apply_outcome(webhook, row, outcome)
                log.info(
                    "webhook.delivered" if outcome.ok else "webhook.delivery_failed",
                    webhook_id=str(webhook.id),
                    delivery_id=str(row.id),
                    status=outcome.status_code,
                    attempts=row.attempts,
                )

    return len(deliveries)


async def sweep_attachments() -> int:
    """확정되지 않은 첨부를 치운다.

    업로드 중 창을 닫으면 pending 행과 (드물게) 반쯤 올라간 객체가 남는다.
    안 치우면 테이블에도 스토리지에도 계속 쌓인다.
    """
    settings = get_settings()
    store = ObjectStore(settings)
    async with session_scope() as session:
        removed = await AttachmentService(session, store).sweep_pending()
    return removed


async def sweep() -> dict[str, int]:
    """주기 실행 진입점. 파이프라인을 한 번씩 돌린다."""
    started = utcnow()
    processed = await drain_outbox()
    delivered = await deliver_webhooks()
    attachments = await sweep_attachments()
    elapsed = (utcnow() - started).total_seconds()
    if processed or delivered or attachments:
        log.info(
            "worker.sweep",
            outbox=processed,
            webhooks=delivered,
            attachments=attachments,
            duration_s=round(elapsed, 2),
        )
    return {"outbox": processed, "webhooks": delivered, "attachments": attachments}


# ── arq 진입점 ──────────────────────────────────────────────────
# arq 의 WorkerCoroutine 프로토콜은 `(ctx, *args, **kwargs)` 를 요구한다.
# 순수 함수와 분리해 두면 테스트와 CLI 가 arq 를 거치지 않고 그대로 부른다.


async def task_drain_outbox(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await drain_outbox()


async def task_deliver_webhooks(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await deliver_webhooks()


async def task_sweep(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> dict[str, int]:
    return await sweep()


async def task_sweep_attachments(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await sweep_attachments()
