"""notify 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ieum.modules.notify.models import EMAIL_MODES, WATCH_TARGETS


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    title: str
    body: str | None
    link: str | None
    target_type: str | None
    target_id: UUID | None
    actor_id: UUID | None
    read_at: datetime | None
    created_at: datetime


class NotificationPageResponse(BaseModel):
    items: list[NotificationResponse]
    next_cursor: str | None = None
    unread_count: int = 0


class MarkReadRequest(BaseModel):
    #: 비우면 전부 읽음 처리한다.
    ids: list[UUID] | None = None


class PreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    in_app: bool
    email_mode: str
    notify_own_actions: bool
    muted_events: list[str]


class PreferenceUpdateRequest(BaseModel):
    in_app: bool | None = None
    email_mode: str | None = Field(default=None, pattern=f"^({'|'.join(EMAIL_MODES)})$")
    notify_own_actions: bool | None = None
    muted_events: list[str] | None = None


class WatchRequest(BaseModel):
    target_type: str = Field(pattern=f"^({'|'.join(WATCH_TARGETS)})$")
    target_id: UUID


class WatchStatusResponse(BaseModel):
    watching: bool


class WatchStatusesResponse(BaseModel):
    """이 중에 구독 중인 것만. **없는 것은 안 적는다** — 목록이 길어질수록
    "구독 안 함" 을 세는 것이 낭비다."""

    watching: list[UUID]


class WebhookCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(min_length=1)
    #: 최소 16자. 서버는 이 값을 다시 보여주지 않는다.
    secret: str = Field(min_length=16, max_length=200)
    scope_id: UUID | None = None


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scope: str
    scope_id: UUID | None
    url: str
    events: list[str]
    enabled: bool
    consecutive_failures: int
    disabled_reason: str | None
    created_at: datetime
    # secret 은 응답에 절대 넣지 않는다.


class WebhookToggleRequest(BaseModel):
    enabled: bool


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    status: str
    attempts: int
    response_code: int | None
    response_excerpt: str | None
    error: str | None
    next_retry_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime


class DeliveryPageResponse(BaseModel):
    items: list[DeliveryResponse]
    next_cursor: str | None = None


class EventCatalogResponse(BaseModel):
    """웹훅 설정 UI 가 고를 수 있는 이벤트 목록."""

    events: list[dict[str, Any]]
