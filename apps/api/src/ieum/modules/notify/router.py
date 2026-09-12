"""notify HTTP 라우터."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ieum.core.deps import AppSettings, CurrentActor, DbSession, PermissionDep
from ieum.core.events import events as event_registry
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.event_catalog import known_event_types
from ieum.modules.notify.repository import PreferenceRepository
from ieum.modules.notify.schemas import (
    DeliveryPageResponse,
    DeliveryResponse,
    EventCatalogResponse,
    MarkReadRequest,
    NotificationPageResponse,
    NotificationResponse,
    PreferenceResponse,
    PreferenceUpdateRequest,
    WatchRequest,
    WatchStatusesResponse,
    WatchStatusResponse,
    WebhookCreateRequest,
    WebhookResponse,
    WebhookToggleRequest,
)
from ieum.modules.notify.service import (
    NotificationService,
    WatchService,
    WebhookService,
)

notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])
watches_router = APIRouter(prefix="/watches", tags=["notifications"])
webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@notifications_router.get("", response_model=NotificationPageResponse)
async def list_notifications(
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    unread_only: bool = False,
) -> NotificationPageResponse:
    service = NotificationService(session, settings)
    page = await service.list_for(
        actor, PageRequest(limit=limit, cursor=cursor), unread_only=unread_only
    )
    return NotificationPageResponse(
        items=[NotificationResponse.model_validate(n) for n in page.items],
        next_cursor=page.next_cursor,
        unread_count=await service.unread_count(actor),
    )


@notifications_router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    body: MarkReadRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """남의 알림은 읽음 처리할 수 없다 — 쿼리에 user_id 조건이 박혀 있다."""
    await NotificationService(session, settings).mark_read(actor, body.ids)
    await session.commit()


@notifications_router.get("/preferences", response_model=PreferenceResponse)
async def get_preferences(actor: CurrentActor, session: DbSession) -> PreferenceResponse:
    row = await PreferenceRepository(session).get_or_create(actor.user_id)
    await session.commit()
    return PreferenceResponse.model_validate(row)


@notifications_router.patch("/preferences", response_model=PreferenceResponse)
async def update_preferences(
    body: PreferenceUpdateRequest, actor: CurrentActor, session: DbSession
) -> PreferenceResponse:
    row = await PreferenceRepository(session).get_or_create(actor.user_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    await session.commit()
    return PreferenceResponse.model_validate(row)


# ── 워치 ────────────────────────────────────────────────────────


@watches_router.post("", response_model=WatchStatusResponse)
async def start_watching(
    body: WatchRequest, actor: CurrentActor, session: DbSession
) -> WatchStatusResponse:
    """이미 구독 중이어도 200 이다. 중복 요청이 에러면 UI 가 번거로워진다."""
    await WatchService(session).watch(actor, body.target_type, body.target_id)
    await session.commit()
    return WatchStatusResponse(watching=True)


@watches_router.delete("", response_model=WatchStatusResponse)
async def stop_watching(
    body: WatchRequest, actor: CurrentActor, session: DbSession
) -> WatchStatusResponse:
    await WatchService(session).unwatch(actor, body.target_type, body.target_id)
    await session.commit()
    return WatchStatusResponse(watching=False)


@watches_router.get("/status", response_model=WatchStatusResponse)
async def watch_status(
    actor: CurrentActor,
    session: DbSession,
    target_type: str,
    target_id: UUID,
) -> WatchStatusResponse:
    watching = await WatchService(session).is_watching(actor, target_type, target_id)
    return WatchStatusResponse(watching=watching)


@watches_router.get("/statuses", response_model=WatchStatusesResponse)
async def watch_statuses(
    actor: CurrentActor,
    session: DbSession,
    target_type: str,
    target_ids: Annotated[list[UUID], Query()] = [],  # noqa: B006
) -> WatchStatusesResponse:
    """여러 개를 **한 번에** 묻는다.

    `/status` 를 목록에서 행마다 부르면 한 페이지에 스무 번이다. 그 무름은
    이미 세 번 데고 적어 둔 것이라(`ProjectPicker`), 목록에 구독 토글을 달기
    전에 이 문을 먼저 냈다.
    """
    watching = await WatchService(session).watching_among(actor, target_type, target_ids)
    return WatchStatusesResponse(watching=sorted(watching))


# ── 웹훅 ────────────────────────────────────────────────────────


@webhooks_router.get("/events", response_model=EventCatalogResponse)
async def event_catalog(actor: CurrentActor) -> EventCatalogResponse:
    """구독할 수 있는 이벤트 목록. 설정 UI 가 이걸로 체크박스를 그린다."""
    return EventCatalogResponse(
        events=[
            {"type": event_type, "aggregate": cls.aggregate_type}
            for event_type in sorted(known_event_types())
            if (cls := event_registry.event_class(event_type)) is not None
        ]
    )


@webhooks_router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> WebhookResponse:
    """시크릿은 응답에 담기지 않는다. 만든 사람이 보관해야 한다."""
    row = await WebhookService(session, settings, permissions).create(
        actor,
        name=body.name,
        url=body.url,
        events=body.events,
        secret=body.secret,
        scope_id=body.scope_id,
    )
    await session.commit()
    return WebhookResponse.model_validate(row)


@webhooks_router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> list[WebhookResponse]:
    rows = await WebhookService(session, settings, permissions).list_for(actor)
    return [WebhookResponse.model_validate(r) for r in rows]


@webhooks_router.post("/{webhook_id}/enabled", response_model=WebhookResponse)
async def toggle_webhook(
    webhook_id: UUID,
    body: WebhookToggleRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> WebhookResponse:
    row = await WebhookService(session, settings, permissions).set_enabled(
        actor, webhook_id, body.enabled
    )
    await session.commit()
    return WebhookResponse.model_validate(row)


@webhooks_router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> None:
    await WebhookService(session, settings, permissions).delete(actor, webhook_id)
    await session.commit()


@webhooks_router.get("/{webhook_id}/deliveries", response_model=DeliveryPageResponse)
async def list_deliveries(
    webhook_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> DeliveryPageResponse:
    """전송 로그. 실패 원인과 다음 재시도 시각까지 보여준다."""
    page = await WebhookService(session, settings, permissions).deliveries(
        actor, webhook_id, PageRequest(limit=limit, cursor=cursor)
    )
    return DeliveryPageResponse(
        items=[DeliveryResponse.model_validate(d) for d in page.items],
        next_cursor=page.next_cursor,
    )
