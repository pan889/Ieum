"""앱 라우터 (M6 "플러그인 훅").

**표면이 둘이다.** 사람이 쓰는 `/apps` 와 앱이 쓰는 `/apps/self` 다.

나눠 둔 이유: 앱 토큰은 사람의 액터가 아니다. 한 라우터에 섞으면 어떤
엔드포인트가 어느 인증을 받는지 읽는 사람이 알 수 없고, 그러다 사람용
엔드포인트에 앱 토큰이 통하는 실수가 난다. 그 실수는 조용하다 — 통해 버리면
아무 오류도 안 난다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import AppSettings, CurrentActor, DbSession, PermissionDep
from ieum.core.exceptions import AuthenticationError
from ieum.modules.plugins import slots
from ieum.modules.plugins.models import App, AppSlot
from ieum.modules.plugins.service import (
    MAX_BODY_CHARS,
    AppService,
    AppTokenService,
    AppView,
    Contribution,
    NewSlot,
)

apps_router = APIRouter(prefix="/apps", tags=["plugins"])


class SlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slot: str
    kind: str
    label: str
    url_template: str | None
    position: int

    @classmethod
    def of(cls, row: AppSlot) -> SlotResponse:
        return cls.model_validate(row)


class StreamResponse(BaseModel):
    """이벤트를 받는 자리의 상태. 앱 화면이 "받고 있나" 를 말한다."""

    url: str
    events: list[str]
    enabled: bool
    consecutive_failures: int
    disabled_reason: str | None


class AppResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    homepage_url: str | None
    enabled: bool
    #: 토큰 앞머리만. 원문은 발급 때 한 번 나가고 우리도 모른다.
    token_prefix: str
    last_seen_at: datetime | None
    created_at: datetime
    slots: list[SlotResponse]
    stream: StreamResponse | None

    @classmethod
    def of(cls, view: AppView) -> AppResponse:
        return cls(
            id=view.app.id,
            name=view.app.name,
            slug=view.app.slug,
            description=view.app.description,
            homepage_url=view.app.homepage_url,
            enabled=view.app.enabled,
            token_prefix=view.app.token_prefix,
            last_seen_at=view.app.last_seen_at,
            created_at=view.app.created_at,
            slots=[SlotResponse.of(row) for row in view.slots],
            stream=(
                None
                if view.stream is None
                else StreamResponse(
                    url=view.stream.url,
                    events=list(view.stream.events),
                    enabled=view.stream.enabled,
                    consecutive_failures=view.stream.consecutive_failures,
                    disabled_reason=view.stream.disabled_reason,
                )
            ),
        )


class IssuedAppResponse(BaseModel):
    """등록 직후에만 나가는 모양. **토큰이 여기 한 번 실린다.**"""

    app: AppResponse
    token: str


class AppCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    homepage_url: str | None = Field(default=None, max_length=2000)
    #: 받을 서버 이벤트. 비우면 이벤트를 안 받는 앱이다(패널만 쓴다).
    events: list[str] = Field(default_factory=list, max_length=64)
    event_url: str | None = Field(default=None, max_length=2000)


class AppUpdateRequest(BaseModel):
    """`None` 은 "안 건드린다" 다 (CLAUDE.md 부분 수정 규칙)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    events: list[str] | None = Field(default=None, max_length=64)
    event_url: str | None = Field(default=None, max_length=2000)


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SlotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=60)
    url_template: str | None = Field(default=None, max_length=2000)
    position: int = Field(default=0, ge=0, le=9999)


class ContributionResponse(BaseModel):
    """화면 한 자리에 실릴 것. 링크면 `url`, 패널이면 `body` 가 찬다."""

    app_slug: str
    app_name: str
    slot: str
    kind: str
    label: str
    url: str | None
    body: str | None

    @classmethod
    def of(cls, row: Contribution) -> ContributionResponse:
        return cls(
            app_slug=row.app_slug,
            app_name=row.app_name,
            slot=row.slot,
            kind=row.kind,
            label=row.label,
            url=row.url,
            body=row.body,
        )


class PanelWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=60)
    #: 마크다운이다. HTML 을 받지 않는다 — slots.py 머리에 이유가 있다.
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)


# ── 고정 경로가 `{app_id}` 형제 전부보다 위다 ──────────────────


@apps_router.get("/slots", response_model=list[dict[str, Any]])
async def list_slot_catalog(actor: CurrentActor) -> list[dict[str, Any]]:
    """서버가 아는 자리 목록. 관리 화면이 고를 수 있는 것만 보여 준다.

    화면이 자리 이름을 자기가 들고 있으면 서버가 자리를 늘렸을 때 조용히
    어긋난다. 한 곳에서 읽는다.
    """
    del actor  # 로그인만 확인한다. 어휘는 비밀이 아니다.
    return slots.catalog()


@apps_router.get("/contributions/issue/{issue_id}", response_model=list[ContributionResponse])
async def list_issue_contributions(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    issue_id: UUID,
) -> list[ContributionResponse]:
    """이 이슈에서 앱들이 놓은 것. **이슈를 볼 수 있는 사람만 본다.**"""
    found = await AppService(session, permissions, settings).contributions_for_issue(
        actor, issue_id
    )
    return [ContributionResponse.of(row) for row in found]


@apps_router.get("/contributions/settings", response_model=list[ContributionResponse])
async def list_settings_contributions(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
) -> list[ContributionResponse]:
    found = await AppService(session, permissions, settings).links_for(actor, "settings.link")
    return [ContributionResponse.of(row) for row in found]


# ── 앱이 자기 토큰으로 쓰는 자리 ───────────────────────────────


async def _app_of(session: DbSession, authorization: str | None) -> App:
    """`Authorization: Bearer ieum_app_...` 로 앱을 복원한다.

    사람의 액터를 만들지 않는다. 앱은 사람이 아니고, 여기서 할 수 있는 일은
    아래 두 엔드포인트뿐이다.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("앱 토큰이 필요하다.", code="auth.invalid_token")
    raw = authorization[7:].strip()
    if not raw.startswith(AppTokenService.PREFIX):
        raise AuthenticationError("앱 토큰이 아니다.", code="auth.invalid_token")
    return await AppTokenService(session).authenticate(raw)


@apps_router.put("/self/panels", status_code=status.HTTP_204_NO_CONTENT)
async def write_own_panel(
    session: DbSession,
    payload: PanelWriteRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """앱이 이슈 하나의 자기 패널에 글을 쓴다. 같은 이슈에 다시 쓰면 덮는다."""
    app = await _app_of(session, authorization)
    await AppTokenService(session).write_panel(
        app, issue_key=payload.issue_key, label=payload.label, body=payload.body
    )
    await session.commit()


@apps_router.delete("/self/panels", status_code=status.HTTP_204_NO_CONTENT)
async def clear_own_panel(
    session: DbSession,
    issue_key: str,
    label: str,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    app = await _app_of(session, authorization)
    await AppTokenService(session).clear_panel(app, issue_key=issue_key, label=label)
    await session.commit()


# ── 사람이 쓰는 자리 ───────────────────────────────────────────


@apps_router.get("", response_model=list[AppResponse])
async def list_apps(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
) -> list[AppResponse]:
    found = await AppService(session, permissions, settings).list_all(actor)
    return [AppResponse.of(view) for view in found]


@apps_router.post("", response_model=IssuedAppResponse, status_code=status.HTTP_201_CREATED)
async def register_app(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    payload: AppCreateRequest,
) -> IssuedAppResponse:
    """앱을 등록한다. **토큰은 이 응답에만 실린다.**"""
    service = AppService(session, permissions, settings)
    row, token = await service.register(
        actor,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        homepage_url=payload.homepage_url,
        events=payload.events,
        event_url=payload.event_url,
    )
    await session.commit()
    view = await service.get(actor, row.id)
    return IssuedAppResponse(app=AppResponse.of(view), token=token)


@apps_router.get("/{app_id}", response_model=AppResponse)
async def get_app(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
) -> AppResponse:
    return AppResponse.of(await AppService(session, permissions, settings).get(actor, app_id))


@apps_router.patch("/{app_id}", response_model=AppResponse)
async def update_app(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
    payload: AppUpdateRequest,
) -> AppResponse:
    service = AppService(session, permissions, settings)
    await service.update(
        actor,
        app_id,
        name=payload.name,
        description=payload.description,
        events=payload.events,
        event_url=payload.event_url,
    )
    await session.commit()
    return AppResponse.of(await service.get(actor, app_id))


@apps_router.post("/{app_id}/enabled", response_model=AppResponse)
async def set_app_enabled(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
    payload: EnabledRequest,
) -> AppResponse:
    service = AppService(session, permissions, settings)
    await service.set_enabled(actor, app_id, enabled=payload.enabled)
    await session.commit()
    return AppResponse.of(await service.get(actor, app_id))


@apps_router.post("/{app_id}/token", response_model=dict[str, str])
async def rotate_app_token(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
) -> dict[str, str]:
    """토큰을 새로 낸다. 옛 토큰은 즉시 죽는다."""
    token = await AppService(session, permissions, settings).rotate_token(actor, app_id)
    await session.commit()
    return {"token": token}


@apps_router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_app(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
) -> None:
    await AppService(session, permissions, settings).remove(actor, app_id)
    await session.commit()


@apps_router.post(
    "/{app_id}/slots", response_model=SlotResponse, status_code=status.HTTP_201_CREATED
)
async def place_slot(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
    payload: SlotCreateRequest,
) -> SlotResponse:
    """앱에게 자리 하나를 준다. **자리는 관리자가 정한다** (service.py 머리)."""
    row = await AppService(session, permissions, settings).place(
        actor,
        app_id,
        NewSlot(
            slot=payload.slot,
            kind=payload.kind,
            label=payload.label,
            url_template=payload.url_template,
            position=payload.position,
        ),
    )
    await session.commit()
    return SlotResponse.of(row)


@apps_router.delete("/{app_id}/slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unplace_slot(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    app_id: UUID,
    slot_id: UUID,
) -> None:
    await AppService(session, permissions, settings).unplace(actor, app_id, slot_id)
    await session.commit()
