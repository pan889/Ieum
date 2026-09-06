"""이벤트 구독.

**타입 문자열로 구독한다.** 이벤트 클래스를 import 하면 notify → issues
방향 의존이 생기는데, 그건 의존 그래프에 없는 화살표다(overview.md).

핸들러는 알림 행과 웹훅 전송 행을 **만들기만** 한다. 실제 발송(SMTP·HTTP)은
워커의 다음 단계가 한다 — 여기서 네트워크를 타면 느린 엔드포인트 하나가
이벤트 디스패치 전체를 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.logging import get_logger
from ieum.modules.notify.models import WebhookDelivery
from ieum.modules.notify.repository import DeliveryRepository, WebhookRepository
from ieum.modules.notify.service import (
    WATCH_TARGET_ISSUE,
    NotificationRequest,
    NotificationService,
    WatchService,
)

log = get_logger(__name__)

#: 이벤트 타입 → (알림 종류, 제목 키). 여기 없는 이벤트는 알림을 만들지 않는다.
ISSUE_NOTIFICATIONS: dict[str, tuple[str, str]] = {
    "issue.created": ("issue.created", "notifications:issue.created"),
    "issue.updated": ("issue.updated", "notifications:issue.updated"),
    "issue.transitioned": ("issue.transitioned", "notifications:issue.transitioned"),
    "issue.commented": ("issue.commented", "notifications:issue.commented"),
}


@dataclass(slots=True)
class HandlerContext:
    """핸들러가 쓰는 것. 워커가 이벤트마다 만들어 넘긴다."""

    session: AsyncSession
    settings: Settings


async def handle_issue_event(ctx: HandlerContext, envelope: EventEnvelope) -> list[UUID]:
    """이슈 이벤트 → 알림. 만들어진 알림 id 를 돌려준다."""
    mapping = ISSUE_NOTIFICATIONS.get(envelope.event_type)
    if mapping is None:
        return []

    kind, title_key = mapping
    actor_id = envelope.uuid("actor_id")

    # 내부 노트는 알림도 내부 사람에게만 가야 한다. 수신자 권한을 여기서
    # 판정할 수 없으므로 M1 에서는 아예 알림을 만들지 않는다.
    if envelope.event_type == "issue.commented" and envelope.get("is_internal"):
        return []

    recipients = await _recipients(ctx, envelope)
    if not recipients:
        return []

    issue_key = str(envelope.get("issue_key", ""))
    service = NotificationService(ctx.session, ctx.settings)
    created = await service.fan_out(
        recipients,
        NotificationRequest(
            kind=kind,
            title_key=title_key,
            body_key=None,
            params={
                "key": issue_key,
                "summary": str(envelope.get("summary", "")),
                "state": str(envelope.get("to_state", "")),
            },
            link=f"/issues/{issue_key}",
            target_type="issue",
            target_id=envelope.aggregate_id,
            actor_id=actor_id,
        ),
    )
    return [n.id for n in created]


async def _recipients(ctx: HandlerContext, envelope: EventEnvelope) -> set[UUID]:
    """워처 + 담당자 + 보고자.

    담당자·보고자는 이벤트 페이로드에 실려 온다. 이슈를 되짚어 읽으면
    모듈 경계를 넘는다.
    """
    watchers = await WatchService(ctx.session).watchers_of(
        WATCH_TARGET_ISSUE, envelope.aggregate_id
    )
    for key in ("assignee_id", "reporter_id"):
        found = envelope.uuid(key)
        if found is not None:
            watchers.add(found)
    return watchers


async def enqueue_webhooks(ctx: HandlerContext, envelope: EventEnvelope) -> int:
    """이 이벤트를 구독하는 웹훅마다 전송 행을 만든다.

    같은 (webhook, event) 조합은 한 번만 만든다. 아웃박스가 재시도될 때
    중복 전송이 나가면 수신자 쪽에서 같은 일을 두 번 하게 된다.
    """
    project_id = envelope.uuid("project_id")
    webhooks = await WebhookRepository(ctx.session).subscribers_of(
        envelope.event_type, project_id=project_id
    )
    if not webhooks:
        return 0

    deliveries = DeliveryRepository(ctx.session)
    created = 0
    for webhook in webhooks:
        if await deliveries.exists(webhook.id, envelope.id):
            continue
        deliveries.add(
            WebhookDelivery(
                webhook_id=webhook.id,
                event_id=envelope.id,
                event_type=envelope.event_type,
                payload=envelope.payload,
            )
        )
        created += 1
    if created:
        log.info("webhook.enqueued", event_type=envelope.event_type, deliveries=created)
    return created
