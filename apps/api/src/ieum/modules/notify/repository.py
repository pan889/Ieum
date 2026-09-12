"""notify 데이터 접근."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page, PageRequest
from ieum.core.time import utcnow
from ieum.modules.notify.models import (
    Notification,
    NotificationPreference,
    Watch,
    Webhook,
    WebhookDelivery,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, notification: Notification) -> Notification:
        self._s.add(notification)
        return notification

    async def get(self, notification_id: UUID) -> Notification | None:
        return await self._s.get(Notification, notification_id)

    async def list_page(
        self, user_id: UUID, request: PageRequest, *, unread_only: bool = False
    ) -> Page[Notification]:
        stmt: Select[tuple[Notification]] = select(Notification).where(
            Notification.user_id == user_id
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        payload = request.cursor_payload
        if payload:
            # UUIDv7 이라 id 역순이 곧 최신순이다.
            stmt = stmt.where(Notification.id < UUID(payload["id"]))
        stmt = stmt.order_by(Notification.id.desc()).limit(request.fetch_limit)
        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(rows, request, lambda n: {"id": str(n.id)})

    async def since(self, user_id: UUID, after: datetime | None, limit: int) -> list[Notification]:
        """지난번 다이제스트 뒤에 쌓인 것. 읽은 것은 뺀다.

        이미 읽었으면 화면에서 봤다는 뜻이다. 그걸 다시 메일로 보내면
        다이제스트가 "아까 본 것 목록" 이 된다.
        """
        stmt = select(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
        if after is not None:
            stmt = stmt.where(Notification.created_at > after)
        stmt = stmt.order_by(Notification.created_at.asc()).limit(limit)
        return list((await self._s.execute(stmt)).scalars().all())

    async def count_since(self, user_id: UUID, after: datetime | None) -> int:
        """`since` 와 **같은 조건**으로 센다. 가져오는 줄 수에 걸리지 않는다.

        다이제스트 본문은 스무 줄까지만 싣지만, "몇 건인지" 는 사실대로 적어야
        한다. 가져온 행을 세면 500건이 쌓인 사람에게 "21건" 이라고 적고
        "그리고 1건 더" 라고 덧붙이게 된다.
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        if after is not None:
            stmt = stmt.where(Notification.created_at > after)
        return int((await self._s.execute(stmt)).scalar_one())

    async def unread_count(self, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def mark_read(self, user_id: UUID, ids: list[UUID] | None = None) -> int:
        stmt = (
            update(Notification)
            .where(Notification.user_id == user_id)
            .where(Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        if ids is not None:
            stmt = stmt.where(Notification.id.in_(ids))
        result: Any = await self._s.execute(stmt)
        return int(result.rowcount or 0)


class WatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def watchers_of(self, target_type: str, target_id: UUID) -> set[UUID]:
        stmt = (
            select(Watch.user_id)
            .where(Watch.target_type == target_type)
            .where(Watch.target_id == target_id)
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def is_watching(self, user_id: UUID, target_type: str, target_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Watch)
            .where(Watch.user_id == user_id)
            .where(Watch.target_type == target_type)
            .where(Watch.target_id == target_id)
        )
        return bool((await self._s.execute(stmt)).scalar_one())

    async def watching_among(
        self, user_id: UUID, target_type: str, target_ids: Sequence[UUID]
    ) -> set[UUID]:
        """이 중에 구독 중인 것만. **목록 화면이 행마다 묻지 않게 한다.**

        한 페이지가 스무 줄이면 질의도 스무 번이 된다. 그 무름은 이 저장소에
        이미 세 번 데고 적어 둔 것(`ProjectPicker`)과 같은 종류라, 목록을
        만들기 전에 이 문을 먼저 낸다.
        """
        # **이 줄은 결과를 바꾸지 않는다** — SQLAlchemy 는 빈 `IN ()` 도
        # 안전하게 컴파일해서 빈 집합을 준다. 아끼는 것은 DB 왕복 한 번이고,
        # 목록이 비었을 때(프로젝트가 하나도 없을 때) 그렇다.
        #
        # 그래서 이 줄을 지키는 시험은 두지 않았다. 써 봤는데 되돌려도
        # 초록이었다 — 한 줄을 고쳐서 깨뜨릴 수 없는 것을 지키는 시험은
        # 있으나 마나 하고, 있으면 지켜지는 줄 알게 된다.
        if not target_ids:
            return set()
        stmt = (
            select(Watch.target_id)
            .where(Watch.user_id == user_id)
            .where(Watch.target_type == target_type)
            .where(Watch.target_id.in_(list(target_ids)))
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def add(self, user_id: UUID, target_type: str, target_id: UUID) -> bool:
        """이미 있으면 False. 중복 요청이 에러가 되면 UI 가 번거로워진다."""
        if await self.is_watching(user_id, target_type, target_id):
            return False
        self._s.add(Watch(user_id=user_id, target_type=target_type, target_id=target_id))
        return True

    async def remove(self, user_id: UUID, target_type: str, target_id: UUID) -> bool:
        stmt = (
            select(Watch)
            .where(Watch.user_id == user_id)
            .where(Watch.target_type == target_type)
            .where(Watch.target_id == target_id)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._s.delete(row)
        return True

    async def list_for(self, user_id: UUID, target_type: str) -> list[UUID]:
        stmt = (
            select(Watch.target_id)
            .where(Watch.user_id == user_id)
            .where(Watch.target_type == target_type)
        )
        return list((await self._s.execute(stmt)).scalars().all())


class PreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_users(self, user_ids: set[UUID]) -> dict[UUID, NotificationPreference]:
        if not user_ids:
            return {}
        stmt = select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids))
        rows = (await self._s.execute(stmt)).scalars().all()
        return {row.user_id: row for row in rows}

    async def daily_digest_users(self) -> list[NotificationPreference]:
        """하루 한 통으로 받기로 한 사람들."""
        stmt = select(NotificationPreference).where(NotificationPreference.email_mode == "daily")
        return list((await self._s.execute(stmt)).scalars().all())

    async def get_or_create(self, user_id: UUID) -> NotificationPreference:
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = NotificationPreference(user_id=user_id)
            self._s.add(row)
            await self._s.flush()
        return row


class WebhookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, webhook_id: UUID) -> Webhook | None:
        return await self._s.get(Webhook, webhook_id)

    def add(self, webhook: Webhook) -> Webhook:
        self._s.add(webhook)
        return webhook

    async def subscribers_of(self, event_type: str, *, project_id: UUID | None) -> list[Webhook]:
        """이 이벤트를 구독하는 활성 웹훅.

        전역 웹훅 + 해당 프로젝트 웹훅. events 배열에 타입이 들어 있어야 한다 —
        빈 배열은 '전체 구독'이 아니라 '아무것도 안 받음'이다.
        """
        stmt = select(Webhook).where(Webhook.enabled.is_(True))
        if project_id is not None:
            stmt = stmt.where(
                (Webhook.scope == "global")
                | ((Webhook.scope == "project") & (Webhook.scope_id == project_id))
            )
        else:
            stmt = stmt.where(Webhook.scope == "global")
        rows = (await self._s.execute(stmt)).scalars().all()
        return [w for w in rows if event_type in (w.events or [])]

    async def list_visible(self, *, project_ids: frozenset[UUID] | None) -> list[Webhook]:
        """볼 수 있는 웹훅. `project_ids` 가 None 이면 전역 권한이라 전부다.

        **전역 웹훅은 프로젝트 권한만 있는 사람에게 안 보인다.** 목록에는
        URL 이 그대로 실리는데, 조직 전체에 걸린 연동 주소에는 대개 경로나
        질의에 비밀이 섞여 있다(Slack·Teams 의 수신 주소가 그렇다). 한
        프로젝트의 관리자가 조직 전체의 연동 주소를 볼 이유는 없다.

        단건 조회(`get`)는 이미 스코프를 제대로 본다 — 목록만 넓었다.
        """
        stmt = select(Webhook).order_by(Webhook.name)
        if project_ids is not None:
            stmt = stmt.where(Webhook.scope_id.in_(project_ids))
        return list((await self._s.execute(stmt)).scalars().all())


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, delivery: WebhookDelivery) -> WebhookDelivery:
        self._s.add(delivery)
        return delivery

    async def exists(self, webhook_id: UUID, event_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .where(WebhookDelivery.event_id == event_id)
        )
        return bool((await self._s.execute(stmt)).scalar_one())

    async def due(self, *, now: datetime | None = None, limit: int = 50) -> list[WebhookDelivery]:
        """전송할 차례가 된 것. SKIP LOCKED 로 워커 중복을 막는다."""
        stmt = (
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending")
            .where(
                WebhookDelivery.next_retry_at.is_(None)
                | (WebhookDelivery.next_retry_at <= (now or utcnow()))
            )
            .order_by(WebhookDelivery.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def list_for_webhook(
        self, webhook_id: UUID, request: PageRequest
    ) -> Page[WebhookDelivery]:
        stmt: Select[tuple[WebhookDelivery]] = select(WebhookDelivery).where(
            WebhookDelivery.webhook_id == webhook_id
        )
        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(WebhookDelivery.id < UUID(payload["id"]))
        stmt = stmt.order_by(WebhookDelivery.id.desc()).limit(request.fetch_limit)
        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(rows, request, lambda d: {"id": str(d.id)})
