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
    WATCH_TARGET_PAGE,
    WATCH_TARGET_PROJECT,
    WATCH_TARGET_SPACE,
    NotificationRequest,
    NotificationService,
    WatchService,
)

log = get_logger(__name__)

#: 멘션 알림 종류. 워처 알림과 따로 두어 사용자가 따로 끌 수 있게 한다.
MENTION_KIND = "issue.mentioned"
PAGE_MENTION_KIND = "wiki.mentioned"

#: 이벤트 타입 → (알림 종류, 제목 키). 여기 없는 이벤트는 알림을 만들지 않는다.
ISSUE_NOTIFICATIONS: dict[str, tuple[str, str]] = {
    "issue.created": ("issue.created", "notifications:issue.created"),
    "issue.updated": ("issue.updated", "notifications:issue.updated"),
    "issue.transitioned": ("issue.transitioned", "notifications:issue.transitioned"),
    "issue.commented": ("issue.commented", "notifications:issue.commented"),
    # SLA 위반 (C5). **같은 표에 둔다** — 알림 경로가 하나여야 하고, 두 길로
    # 만들면 환경설정(메일 끄기·워치)이 한쪽만 적용된다. `_recipients` 가
    # 페이로드의 `assignee_id` 를 이미 더하므로, 담당자는 워치하지 않아도
    # 받는다. 담당자가 없으면 워처(= 큐를 보는 사람들)에게 간다.
    "desk.sla.breached": ("desk.sla.breached", "notifications:sla.breached"),
    # 에스컬레이션 (C5). 제목은 **조치마다 다르다** — `_escalation_title` 이
    # 고른다. 표에는 기본값을 둔다: 조치를 늘리면서 제목을 안 더해도 알림
    # 자체는 나가야 한다(빠진 것은 문구이고, 사람을 부르는 일 자체가 아니다).
    "desk.sla.escalated": ("desk.sla.escalated", "notifications:sla.escalated"),
    # 승인 (C12). **같은 표에 둔다** — 알림 경로가 하나여야 한다.
    #
    # 승인 요청의 수신자는 워처가 아니라 **찍어 둔 승인자들**이다. 그 사람들은
    # 티켓을 보고 있지 않고(부서장·고객이다), 지목하지 않으면 결정할 사람이
    # 아무 소식을 못 받는다. `_recipients` 가 `to_user_ids` 를 읽는다.
    "desk.approval.requested": ("desk.approval.requested", "notifications:approval.requested"),
    # 결정은 반대다: 기다리던 사람들(보고자·담당자·워처)에게 간다.
    "desk.approval.decided": ("desk.approval.decided", "notifications:approval.decided"),
}

#: 결정 결과별 제목. 승인과 거절은 사람이 해야 할 다음 일이 다르다 —
#: 거절은 이유를 읽고 다시 낼지 정해야 한다. 한 문구로 묶으면 알림을 열어
#: 봐야 어느 쪽인지 안다.
DECISION_TITLES: dict[str, str] = {
    "approved": "notifications:approval.approved",
    "declined": "notifications:approval.declined",
}

#: 조치별 제목. 없으면 위 표의 기본값을 쓴다.
ESCALATION_TITLES: dict[str, str] = {
    "notify": "notifications:sla.escalated.notify",
    "raise_priority": "notifications:sla.escalated.raisePriority",
}

PAGE_NOTIFICATIONS: dict[str, tuple[str, str]] = {
    "wiki.page.published": ("wiki.page.published", "notifications:wiki.published"),
    "wiki.page.updated": ("wiki.page.updated", "notifications:wiki.updated"),
    "wiki.page.commented": ("wiki.page.commented", "notifications:wiki.commented"),
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

    watchers = await _recipients(ctx, envelope)
    # 멘션은 issues 가 이미 볼 권한을 확인해서 실어 보낸다 (notify 는 이슈
    # ACL 을 못 본다). 본인이 자기를 멘션한 건 알릴 필요가 없다.
    mentioned = {uid for uid in envelope.uuid_list("mentioned_ids") if uid != actor_id}
    # 멘션된 사람에게는 멘션 알림 하나만 간다. 워처이기도 하다고 두 번 보내면
    # 같은 일로 알림이 두 개 쌓인다.
    watchers -= mentioned
    if not watchers and not mentioned:
        return []

    issue_key = str(envelope.get("issue_key", ""))
    params = {
        "key": issue_key,
        "summary": str(envelope.get("summary", "")),
        "state": str(envelope.get("to_state", "")),
        # 에스컬레이션만 쓴다. 다른 알림의 제목은 이 값을 참조하지 않는다.
        "percent": str(envelope.get("at_percent", "")),
        "priority": str(envelope.get("priority", "")),
    }
    if envelope.event_type == "desk.sla.escalated":
        title_key = ESCALATION_TITLES.get(str(envelope.get("action", "")), title_key)
    if envelope.event_type == "desk.approval.decided":
        title_key = DECISION_TITLES.get(str(envelope.get("status", "")), title_key)
    service = NotificationService(ctx.session, ctx.settings)
    created = []

    if watchers:
        created += await service.fan_out(
            watchers,
            NotificationRequest(
                kind=kind,
                title_key=title_key,
                body_key=None,
                params=params,
                link=f"/issues/{issue_key}",
                target_type="issue",
                target_id=envelope.aggregate_id,
                actor_id=actor_id,
            ),
        )

    if mentioned:
        created += await service.fan_out(
            mentioned,
            NotificationRequest(
                kind=MENTION_KIND,
                title_key="notifications:issue.mentioned",
                body_key=None,
                params=params,
                link=f"/issues/{issue_key}",
                target_type="issue",
                target_id=envelope.aggregate_id,
                actor_id=actor_id,
            ),
        )

    return [n.id for n in created]


async def _recipients(ctx: HandlerContext, envelope: EventEnvelope) -> set[UUID]:
    """이슈 워처 + **프로젝트 워처** + 담당자 + 보고자.

    담당자·보고자는 이벤트 페이로드에 실려 온다. 이슈를 되짚어 읽으면
    모듈 경계를 넘는다.
    """
    watches = WatchService(ctx.session)
    watchers = await watches.watchers_of(WATCH_TARGET_ISSUE, envelope.aggregate_id)
    # **프로젝트를 보고 있으면 그 안의 이슈 소식을 받는다.** 문서 쪽이 이미
    # 그렇게 한다(문서 워처 + 스페이스 워처) — 이슈만 그러지 않고 있었다.
    #
    # 이건 빠진 기능이 아니라 **거짓말이었다**: `watch.target_type` 은 이미
    # `project` 를 받고, API 도 받아서 저장했다. 저장은 되는데 아무도 안 읽으니
    # 구독한 사람은 목록에서 자기 구독을 보면서 소식은 하나도 못 받는다.
    # 아무 일도 안 일어나는 것보다 나쁘다 — 구독했다고 믿으니까.
    project_id = envelope.uuid("project_id")
    if project_id is not None:
        watchers |= await watches.watchers_of(WATCH_TARGET_PROJECT, project_id)
    # `to_user_id` 는 SLA 에스컬레이션 규칙이 **지목한 사람**이다. 워처도
    # 담당자도 아닐 수 있고, 그래서 이 키를 안 읽으면 규칙이 부른 사람에게만
    # 알림이 안 간다 — 규칙 전체가 하는 일이 그것뿐인데.
    for key in ("assignee_id", "reporter_id", "to_user_id"):
        found = envelope.uuid(key)
        if found is not None:
            watchers.add(found)
    # **여럿을 지목하는 이벤트**도 있다 (승인 요청, C12). 승인자는 워처도
    # 담당자도 아닐 수 있고, 이 줄이 없으면 결정할 사람들에게만 알림이 안
    # 간다 — 그 알림이 이 기능이 하는 일의 전부인데.
    watchers |= set(envelope.uuid_list("to_user_ids"))
    return watchers


async def handle_page_event(ctx: HandlerContext, envelope: EventEnvelope) -> list[UUID]:
    """문서 이벤트 → 알림. 만들어진 알림 id 를 돌려준다.

    수신자는 **문서 워처 + 스페이스 워처**다. 스페이스를 보고 있으면 그 안의
    문서 하나하나를 따로 챙기지 않아도 된다 — 트리가 깊어질수록 그게 유일하게
    쓸 만한 구독 단위다.
    """
    mapping = PAGE_NOTIFICATIONS.get(envelope.event_type)
    if mapping is None:
        return []

    kind, title_key = mapping
    actor_id = envelope.uuid("actor_id")
    space_id = envelope.uuid("space_id")

    watches = WatchService(ctx.session)
    watchers = await watches.watchers_of(WATCH_TARGET_PAGE, envelope.aggregate_id)
    if space_id is not None:
        watchers |= await watches.watchers_of(WATCH_TARGET_SPACE, space_id)

    # 멘션은 wiki 가 이미 볼 권한을 확인해서 실어 보낸다 (notify 는 문서
    # 제한을 못 본다). 본인이 자기를 멘션한 건 알릴 필요가 없다.
    mentioned = {uid for uid in envelope.uuid_list("mentioned_ids") if uid != actor_id}
    # 멘션된 사람에게는 멘션 알림 하나만 간다. 워처이기도 하다고 두 번 보내면
    # 같은 일로 알림이 두 개 쌓인다.
    watchers -= mentioned
    if not watchers and not mentioned:
        return []

    space_key = str(envelope.get("space_key", ""))
    path = str(envelope.get("path", ""))
    params = {
        "title": str(envelope.get("title", "")),
        "space": space_key,
        "path": path,
    }
    link = f"/wiki/{space_key}/{path}" if space_key and path else None
    service = NotificationService(ctx.session, ctx.settings)
    created = []

    if watchers:
        created += await service.fan_out(
            watchers,
            NotificationRequest(
                kind=kind,
                title_key=title_key,
                body_key=None,
                params=params,
                link=link,
                target_type="page",
                target_id=envelope.aggregate_id,
                actor_id=actor_id,
            ),
        )

    if mentioned:
        created += await service.fan_out(
            mentioned,
            NotificationRequest(
                kind=PAGE_MENTION_KIND,
                title_key="notifications:wiki.mentioned",
                body_key=None,
                params=params,
                link=link,
                target_type="page",
                target_id=envelope.aggregate_id,
                actor_id=actor_id,
            ),
        )

    return [n.id for n in created]


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
