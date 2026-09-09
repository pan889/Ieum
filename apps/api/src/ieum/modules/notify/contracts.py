"""notify 의 공개 인터페이스.

다른 모듈이 구독 여부를 묻거나, 보낼 메일 한 통을 만들어 넘길 때 쓴다.
메일 **발송**은 여기서 하지 않는다 — 워커가 트랜잭션 밖에서 보낸다.
SMTP 가 느리면 커넥션을 붙잡고 락이 쌓인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.permissions import PermissionService
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


@dataclass(frozen=True, slots=True)
class WebhookHealth:
    """웹훅이 지금 살아 있는가. 앱 화면이 "이벤트를 받고 있나" 를 말할 때 쓴다."""

    enabled: bool
    consecutive_failures: int
    disabled_reason: str | None
    events: tuple[str, ...]
    url: str


async def open_event_stream(
    session: AsyncSession,
    permissions: PermissionService,
    settings: Settings,
    actor: Actor,
    *,
    name: str,
    url: str,
    events: list[str],
    secret: str,
) -> UUID:
    """서버 이벤트를 받을 자리를 하나 연다. 만든 웹훅의 id 를 준다.

    **권한 검사를 건너뛰지 않는다.** `WebhookService` 를 그대로 부르므로
    부르는 사람은 `notify.webhook.manage` 도 갖고 있어야 한다 — 앱 등록
    권한만으로 웹훅을 만들 수 있으면 그게 곧 우회로다.

    plugins 가 이것을 부르는 이유는 자기 배달 경로를 만들지 않기 위해서다.
    서명·재시도·자동 중단·전송 로그가 이미 여기 있다.
    """
    from ieum.modules.notify.service import WebhookService

    row = await WebhookService(session, settings, permissions).create(
        actor, name=name, url=url, events=events, secret=secret
    )
    return row.id


async def retune_event_stream(
    session: AsyncSession,
    permissions: PermissionService,
    settings: Settings,
    actor: Actor,
    webhook_id: UUID,
    *,
    url: str | None = None,
    events: list[str] | None = None,
) -> None:
    """받는 주소나 이벤트 목록을 고친다. `None` 은 "안 건드린다" 다."""
    from ieum.modules.notify.service import WebhookService

    await WebhookService(session, settings, permissions).update(
        actor, webhook_id, url=url, events=events
    )


async def close_event_stream(
    session: AsyncSession,
    permissions: PermissionService,
    settings: Settings,
    actor: Actor,
    webhook_id: UUID,
) -> None:
    from ieum.modules.notify.service import WebhookService

    await WebhookService(session, settings, permissions).delete(actor, webhook_id)


async def set_event_stream_enabled(
    session: AsyncSession,
    permissions: PermissionService,
    settings: Settings,
    actor: Actor,
    webhook_id: UUID,
    *,
    enabled: bool,
) -> None:
    """앱을 끄면 이벤트도 멈춘다. 끈 앱이 계속 받으면 끈 것이 아니다."""
    from ieum.modules.notify.service import WebhookService

    await WebhookService(session, settings, permissions).set_enabled(actor, webhook_id, enabled)


async def event_stream_health(session: AsyncSession, webhook_id: UUID) -> WebhookHealth | None:
    """읽기만 한다 — 앱 목록을 그릴 때 줄마다 부른다."""
    from ieum.modules.notify.models import Webhook

    row = await session.get(Webhook, webhook_id)
    if row is None:
        return None
    return WebhookHealth(
        enabled=row.enabled,
        consecutive_failures=row.consecutive_failures,
        disabled_reason=row.disabled_reason,
        events=tuple(row.events),
        url=row.url,
    )


__all__ = [
    "Mail",
    "WebhookHealth",
    "close_event_stream",
    "event_stream_health",
    "is_watching",
    "open_event_stream",
    "retune_event_stream",
    "set_event_stream_enabled",
    "watch",
    "watchers_of",
]
