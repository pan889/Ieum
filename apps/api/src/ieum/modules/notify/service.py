"""notify 비즈니스 로직.

알림은 **수신자 언어로** 렌더한다 (i18n.md 3절). 발신자 기준으로 렌더하면
한국어 사용자가 만든 이슈의 알림이 영어권 담당자에게 한국어로 간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.exceptions import NotFoundError, ValidationError
from ieum.core.i18n import translator_for
from ieum.core.logging import get_logger
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.identity import contracts as identity
from ieum.modules.notify import permissions as perms
from ieum.modules.notify.mail import Mail
from ieum.modules.notify.models import Notification, Webhook
from ieum.modules.notify.repository import (
    DeliveryRepository,
    NotificationRepository,
    PreferenceRepository,
    WatchRepository,
    WebhookRepository,
)

log = get_logger(__name__)

WATCH_TARGET_ISSUE = "issue"
WATCH_TARGET_PAGE = "page"
WATCH_TARGET_SPACE = "space"


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """알림 하나를 만들 재료. 문구는 아직 번역되지 않았다."""

    kind: str
    #: `notifications:issue.commented.title` 같은 카탈로그 키
    title_key: str
    body_key: str | None = None
    params: dict[str, Any] | None = None
    link: str | None = None
    target_type: str | None = None
    target_id: UUID | None = None
    actor_id: UUID | None = None


class NotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._notifications = NotificationRepository(session)
        self._watches = WatchRepository(session)
        self._preferences = PreferenceRepository(session)

    async def fan_out(
        self, recipients: set[UUID], request: NotificationRequest
    ) -> list[Notification]:
        """수신자마다 자기 언어로 렌더해 알림을 만든다.

        메일은 여기서 보내지 않는다. 발송 대상만 돌려주고 워커가 보낸다 —
        SMTP 가 느리면 이벤트 디스패치 전체가 막힌다.
        """
        if not recipients:
            return []

        preferences = await self._preferences.for_users(recipients)
        created: list[Notification] = []

        for user_id in sorted(recipients):
            preference = preferences.get(user_id)
            # **자기 행동은 기본적으로 안 알린다.** 다만 그것은 **설정**이다 —
            # `notify_own_actions` 를 켠 사람에게는 보낸다.
            #
            # 전에는 여기서 액터를 무조건 뺐고, 그래서 그 설정은 저장되고 화면에
            # 보이면서 **아무 일도 하지 않았다.** 스키마·모델·마이그레이션에만
            # 있고 어떤 로직도 읽지 않는 값이었다.
            if user_id == request.actor_id and not (
                preference is not None and preference.notify_own_actions
            ):
                continue
            if preference is not None:
                if not preference.in_app:
                    continue
                if request.kind in (preference.muted_events or []):
                    continue

            user = await identity.get_user(self._s, user_id)
            if user is None or not user.is_active:
                continue

            translate = translator_for(self._settings.i18n_catalog_dir, user.locale)
            params = request.params or {}
            created.append(
                self._notifications.add(
                    Notification(
                        user_id=user_id,
                        kind=request.kind,
                        title=translate(request.title_key, **params),
                        body=translate(request.body_key, **params) if request.body_key else None,
                        link=request.link,
                        target_type=request.target_type,
                        target_id=request.target_id,
                        actor_id=request.actor_id,
                    )
                )
            )

        # id 는 flush 전까지 None 이다(SQLAlchemy 의 default 는 INSERT 시점 적용).
        # 호출자가 이 id 로 메일 대상을 다시 조회하므로 여기서 밀어야 한다 —
        # 안 그러면 `id IN (NULL)` 이 되어 메일이 조용히 안 나간다.
        await self._s.flush()
        return created

    async def pending_mail(self, notifications: list[Notification]) -> list[Mail]:
        """메일을 보낼 대상만 골라 봉투를 만든다."""
        if not notifications:
            return []
        user_ids = {n.user_id for n in notifications}
        preferences = await self._preferences.for_users(user_ids)

        mails: list[Mail] = []
        for notification in notifications:
            preference = preferences.get(notification.user_id)
            # 즉시 받는 사람만. `daily` 는 다이제스트가 따로 챙긴다.
            if preference is not None and preference.email_mode != "instant":
                continue
            user = await identity.get_user(self._s, notification.user_id)
            if user is None or not user.is_active:
                continue
            mails.append(
                Mail(
                    to=user.email,
                    subject=notification.title,
                    body=notification.body or notification.title,
                    link=notification.link,
                )
            )
        return mails

    # ── 사용자 API ──────────────────────────────────────────────

    async def list_for(
        self, actor: Actor, request: PageRequest, *, unread_only: bool = False
    ) -> Page[Notification]:
        return await self._notifications.list_page(actor.user_id, request, unread_only=unread_only)

    async def unread_count(self, actor: Actor) -> int:
        return await self._notifications.unread_count(actor.user_id)

    async def mark_read(self, actor: Actor, ids: list[UUID] | None = None) -> int:
        """남의 알림은 못 읽는다 — user_id 조건이 쿼리에 박혀 있다."""
        return await self._notifications.mark_read(actor.user_id, ids)


class WatchService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._watches = WatchRepository(session)

    async def watch(self, actor: Actor, target_type: str, target_id: UUID) -> bool:
        self._validate_target(target_type)
        return await self._watches.add(actor.user_id, target_type, target_id)

    async def unwatch(self, actor: Actor, target_type: str, target_id: UUID) -> bool:
        self._validate_target(target_type)
        return await self._watches.remove(actor.user_id, target_type, target_id)

    async def is_watching(self, actor: Actor, target_type: str, target_id: UUID) -> bool:
        return await self._watches.is_watching(actor.user_id, target_type, target_id)

    async def watchers_of(self, target_type: str, target_id: UUID) -> set[UUID]:
        return await self._watches.watchers_of(target_type, target_id)

    def _validate_target(self, target_type: str) -> None:
        from ieum.modules.notify.models import WATCH_TARGETS

        if target_type not in WATCH_TARGETS:
            raise ValidationError(
                f"구독할 수 없는 대상이다: {target_type}",
                code="notify.invalid_watch_target",
                details={"allowed": list(WATCH_TARGETS)},
            )


class WebhookService:
    def __init__(
        self, session: AsyncSession, settings: Settings, permissions: PermissionService
    ) -> None:
        self._s = session
        self._settings = settings
        self._perms = permissions
        self._webhooks = WebhookRepository(session)
        self._deliveries = DeliveryRepository(session)

    def _box(self) -> Any:
        from ieum.core.crypto import SecretBox

        return SecretBox(self._settings.secret_key.get_secret_value(), purpose="notify.webhook")

    async def create(
        self,
        actor: Actor,
        *,
        name: str,
        url: str,
        events: list[str],
        secret: str,
        scope_id: UUID | None = None,
    ) -> Webhook:
        scope = Scope.project(scope_id) if scope_id else Scope.global_()
        await self._perms.require(self._s, actor, perms.WEBHOOK_MANAGE, scope=scope)

        self._validate_url(url)
        self._validate_events(events)
        if len(secret) < 16:
            raise ValidationError(
                "웹훅 시크릿은 16자 이상이어야 한다.",
                code="notify.webhook_secret_too_short",
            )

        row = Webhook(
            name=name,
            scope="project" if scope_id else "global",
            scope_id=scope_id,
            url=url,
            secret_enc=self._box().encrypt(secret),
            events=sorted(set(events)),
            created_by=actor.user_id,
        )
        self._webhooks.add(row)
        await self._s.flush()
        return row

    async def list_for(self, actor: Actor) -> list[Webhook]:
        acl = await self._perms.acl_for(self._s, actor, perms.WEBHOOK_VIEW)
        if acl.is_empty:
            return []
        return await self._webhooks.list_visible(
            project_ids=None if acl.is_global else acl.project_ids
        )

    async def get(self, actor: Actor, webhook_id: UUID) -> Webhook:
        row = await self._webhooks.get(webhook_id)
        if row is None:
            raise NotFoundError("웹훅을 찾을 수 없다.")
        scope = Scope.project(row.scope_id) if row.scope_id else Scope.global_()
        await self._perms.require(self._s, actor, perms.WEBHOOK_VIEW, scope=scope)
        return row

    async def update(
        self,
        actor: Actor,
        webhook_id: UUID,
        *,
        url: str | None = None,
        events: list[str] | None = None,
    ) -> Webhook:
        """받는 주소·이벤트 목록을 고친다. `None` 은 "안 건드린다" 다.

        검사가 `create` 와 같은 자리에 있어야 한다 — 고칠 때만 통과하는
        주소가 생기면 검사가 있는 뜻이 없다.
        """
        row = await self.get(actor, webhook_id)
        scope = Scope.project(row.scope_id) if row.scope_id else Scope.global_()
        await self._perms.require(self._s, actor, perms.WEBHOOK_MANAGE, scope=scope)
        if url is not None:
            self._validate_url(url)
            row.url = url
        if events is not None:
            self._validate_events(events)
            row.events = sorted(set(events))
        return row

    async def set_enabled(self, actor: Actor, webhook_id: UUID, enabled: bool) -> Webhook:
        row = await self.get(actor, webhook_id)
        scope = Scope.project(row.scope_id) if row.scope_id else Scope.global_()
        await self._perms.require(self._s, actor, perms.WEBHOOK_MANAGE, scope=scope)
        row.enabled = enabled
        if enabled:
            # 다시 켤 때 실패 카운터를 지운다. 안 그러면 한 번 더 실패하고 꺼진다.
            row.consecutive_failures = 0
            row.disabled_reason = None
        return row

    async def delete(self, actor: Actor, webhook_id: UUID) -> None:
        row = await self.get(actor, webhook_id)
        scope = Scope.project(row.scope_id) if row.scope_id else Scope.global_()
        await self._perms.require(self._s, actor, perms.WEBHOOK_MANAGE, scope=scope)
        await self._s.delete(row)

    async def deliveries(self, actor: Actor, webhook_id: UUID, request: PageRequest) -> Page[Any]:
        await self.get(actor, webhook_id)
        return await self._deliveries.list_for_webhook(webhook_id, request)

    def _validate_url(self, url: str) -> None:
        """http/https 만 허용한다. file: 나 사설 스킴은 SSRF 통로가 된다."""
        if not url.startswith(("http://", "https://")):
            raise ValidationError(
                "http 또는 https URL 이어야 한다.", code="notify.invalid_webhook_url"
            )
        if len(url) > 2000:
            raise ValidationError("URL 이 너무 길다.", code="notify.invalid_webhook_url")

    def _validate_events(self, events: list[str]) -> None:
        # 카탈로그를 거친다. core.events 를 직접 보면 import 순서에 따라
        # 멀쩡한 이벤트 타입이 "등록되지 않았다"고 거부된다.
        from ieum.event_catalog import known_event_types

        known = known_event_types()
        unknown = sorted(set(events) - known)
        if unknown:
            raise ValidationError(
                "등록되지 않은 이벤트 타입이다.",
                code="notify.unknown_event_type",
                details={"unknown": unknown, "known": sorted(known)},
            )
        if not events:
            raise ValidationError(
                "구독할 이벤트를 하나 이상 골라야 한다.",
                code="notify.no_events_selected",
            )


__all__ = [
    "WATCH_TARGET_ISSUE",
    "WATCH_TARGET_PAGE",
    "WATCH_TARGET_SPACE",
    "NotificationRequest",
    "NotificationService",
    "WatchService",
    "WebhookService",
]
