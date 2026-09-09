"""자산 라우터 (C15).

목록은 **언제나 커서 페이지다.** 자산은 수천 개가 되고, 이 저장소는 "목록을
통째로 받아 드롭다운에 넣는" 실수를 세 번 했다(ux-principles 4절). 상한 없는
전체 목록을 주는 라우트를 두지 않으면 그 실수를 네 번째로 하기 어렵다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import PageRequest
from ieum.modules.desk.assets import (
    AssetService,
    AssetTicket,
    AssetTypeService,
    AssetView,
    LinkedAsset,
    NewAsset,
    OrgChoice,
)
from ieum.modules.desk.models import ASSET_STATUSES, AssetType

assets_router = APIRouter(prefix="/assets", tags=["assets"])

DEFAULT_LIMIT = 25
Limit = Annotated[int, Query(ge=1, le=100)]
_STATUS = f"^({'|'.join(ASSET_STATUSES)})$"


class AssetTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    icon: str | None
    position: int
    is_archived: bool

    @classmethod
    def of(cls, row: AssetType) -> AssetTypeResponse:
        return cls.model_validate(row)


class AssetTypeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=64)
    position: int = Field(default=0, ge=0, le=9999)


class ArchivedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_archived: bool


class AssetResponse(BaseModel):
    id: UUID
    type_id: UUID
    type_name: str
    name: str
    tag: str | None
    status: str
    owner_id: UUID | None
    owner_name: str | None
    organization_id: UUID | None
    organization_name: str | None
    location: str | None
    note: str | None
    #: 이 자산에 걸린 티켓 수. 자꾸 고장나는 장비를 찾는 근거다.
    ticket_count: int
    created_at: datetime

    @classmethod
    def of(cls, view: AssetView) -> AssetResponse:
        row = view.asset
        return cls(
            id=row.id,
            type_id=row.type_id,
            type_name=view.type_name,
            name=row.name,
            tag=row.tag,
            status=row.status,
            owner_id=row.owner_id,
            owner_name=view.owner_name,
            organization_id=row.organization_id,
            organization_name=view.organization_name,
            location=row.location,
            note=row.note,
            ticket_count=view.ticket_count,
            created_at=row.created_at,
        )


class LinkedAssetResponse(BaseModel):
    """티켓에 붙은 자산 한 줄. 티켓 화면이 쓰는 만큼만 담는다 — 메모와 담당자는
    자산 화면의 것이다."""

    id: UUID
    type_name: str
    name: str
    tag: str | None
    status: str
    location: str | None
    organization_name: str | None

    @classmethod
    def of(cls, found: LinkedAsset) -> LinkedAssetResponse:
        row = found.asset
        return cls(
            id=row.id,
            type_name=found.type_name,
            name=row.name,
            tag=row.tag,
            status=row.status,
            location=row.location,
            organization_name=found.organization_name,
        )


class OrgChoiceResponse(BaseModel):
    """자산에 붙일 조직 후보. 이름과 id 뿐이다 — 조직 관리 화면의 나머지
    (소속 인원수·도메인·메모)는 `desk.customer.manage` 의 것이다."""

    id: UUID
    name: str

    @classmethod
    def of(cls, row: OrgChoice) -> OrgChoiceResponse:
        return cls(id=row.id, name=row.name)


class OrgChoicesResponse(BaseModel):
    items: list[OrgChoiceResponse]
    #: 상한에 닿았는가. 화면이 "더 있다" 를 말하는 근거다.
    has_more: bool


class AssetTicketResponse(BaseModel):
    issue_id: UUID
    key: str
    summary: str
    state_name: str
    state_category: str

    @classmethod
    def of(cls, found: AssetTicket) -> AssetTicketResponse:
        return cls(
            issue_id=found.issue_id,
            key=found.key,
            summary=found.summary,
            state_name=found.state_name,
            state_category=found.state_category,
        )


class AssetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: UUID
    name: str = Field(min_length=1, max_length=200)
    tag: str | None = Field(default=None, max_length=64)
    status: str = Field(default="in_use", pattern=_STATUS)
    owner_id: UUID | None = None
    organization_id: UUID | None = None
    location: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)

    def to_payload(self) -> NewAsset:
        return NewAsset(
            type_id=self.type_id,
            name=self.name,
            tag=self.tag,
            status=self.status,
            owner_id=self.owner_id,
            organization_id=self.organization_id,
            location=self.location,
            note=self.note,
        )


class AssetUpdateRequest(BaseModel):
    """부분 수정. 비우는 것은 `clear_*` 로 따로 받는다 — `None` 은 언제나
    "안 건드린다" 다."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    tag: str | None = Field(default=None, max_length=64)
    clear_tag: bool = False
    status: str | None = Field(default=None, pattern=_STATUS)
    owner_id: UUID | None = None
    clear_owner: bool = False
    organization_id: UUID | None = None
    clear_organization: bool = False
    location: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID


# ── 자산 종류 ───────────────────────────────────────────────────
#
# **`/{asset_id}` 형제보다 위에 둔다** — 아래로 내려가면 `types` 가 자산 id 로
# 잡혀 UUID 파싱 오류가 난다 (conventions "고정 경로는 형제 전부보다 위에").


@assets_router.get("/types", response_model=list[AssetTypeResponse])
async def list_asset_types(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    include_archived: bool = False,
) -> list[AssetTypeResponse]:
    rows = await AssetTypeService(session, permissions).list_all(
        actor, include_archived=include_archived
    )
    return [AssetTypeResponse.of(row) for row in rows]


@assets_router.post("/types", response_model=AssetTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_asset_type(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: AssetTypeCreateRequest,
) -> AssetTypeResponse:
    row = await AssetTypeService(session, permissions).create(
        actor, name=payload.name, icon=payload.icon, position=payload.position
    )
    await session.commit()
    return AssetTypeResponse.of(row)


@assets_router.post("/types/{type_id}/archived", response_model=AssetTypeResponse)
async def archive_asset_type(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    type_id: UUID,
    payload: ArchivedRequest,
) -> AssetTypeResponse:
    """접는다. **지우지 않는다** — 그 종류의 자산이 가리킬 곳을 잃는다."""
    row = await AssetTypeService(session, permissions).set_archived(
        actor, type_id, archived=payload.is_archived
    )
    await session.commit()
    return AssetTypeResponse.of(row)


@assets_router.get("/organizations", response_model=OrgChoicesResponse)
async def search_asset_organizations(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> OrgChoicesResponse:
    """자산에 붙일 고객 조직을 찾는다. `desk.asset.manage` 로 열린다.

    조직 관리 목록(`/customer-organizations`)을 쓰지 않는 이유는 step-up
    이다 — `AssetService.organizations` 주석에 적어 두었다.

    `has_more` 를 함께 주는 이유: 잘린 것을 조용히 두면 화면은 "이게 전부"
    라고 보여 준다. 자르는 것은 괜찮고, 말 없이 자르는 것이 문제다.
    """
    found = await AssetService(session, permissions).organizations(actor, query=q, limit=limit)
    return OrgChoicesResponse(
        items=[OrgChoiceResponse.of(row) for row in found.items],
        has_more=found.has_more,
    )


# ── 티켓에 붙은 자산 ────────────────────────────────────────────


@assets_router.get("/linked", response_model=list[LinkedAssetResponse])
async def list_linked_assets(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, issue_id: UUID
) -> list[LinkedAssetResponse]:
    """이 티켓이 무엇에 대한 것인가. **이슈를 볼 수 있어야 본다.**"""
    found = await AssetService(session, permissions).for_issue(actor, issue_id)
    return [LinkedAssetResponse.of(row) for row in found]


@assets_router.post(
    "/linked", response_model=LinkedAssetResponse, status_code=status.HTTP_201_CREATED
)
async def link_asset(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    issue_id: UUID,
    payload: LinkRequest,
) -> LinkedAssetResponse:
    found = await AssetService(session, permissions).link(actor, issue_id, payload.asset_id)
    await session.commit()
    return LinkedAssetResponse.of(found)


@assets_router.delete("/linked", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_asset(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    issue_id: UUID,
    asset_id: UUID,
) -> None:
    await AssetService(session, permissions).unlink(actor, issue_id, asset_id)
    await session.commit()


# ── 자산 ────────────────────────────────────────────────────────


@assets_router.get("")
async def search_assets(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    q: str | None = None,
    type_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", pattern=_STATUS)] = None,
    organization_id: UUID | None = None,
    include_retired: bool = False,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """이름·자산번호·위치로 찾는다. **전체 목록을 주는 라우트는 없다.**"""
    page = await AssetService(session, permissions).search(
        actor,
        PageRequest(limit=limit, cursor=cursor),
        query=q,
        type_id=type_id,
        status=status_filter,
        organization_id=organization_id,
        include_retired=include_retired,
    )
    return {
        "items": [AssetResponse.of(view).model_dump(mode="json") for view in page.items],
        "next_cursor": page.next_cursor,
    }


@assets_router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: AssetCreateRequest,
) -> AssetResponse:
    view = await AssetService(session, permissions).create(actor, payload.to_payload())
    await session.commit()
    return AssetResponse.of(view)


@assets_router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, asset_id: UUID
) -> AssetResponse:
    return AssetResponse.of(await AssetService(session, permissions).get(actor, asset_id))


@assets_router.get("/{asset_id}/tickets", response_model=list[AssetTicketResponse])
async def list_asset_tickets(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, asset_id: UUID
) -> list[AssetTicketResponse]:
    """이 자산에 걸린 티켓. 자꾸 고장나는 장비를 읽는 자리다."""
    found = await AssetService(session, permissions).tickets_of(actor, asset_id)
    return [AssetTicketResponse.of(row) for row in found]


@assets_router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    asset_id: UUID,
    payload: AssetUpdateRequest,
) -> AssetResponse:
    view = await AssetService(session, permissions).update(
        actor,
        asset_id,
        name=payload.name,
        tag=payload.tag,
        clear_tag=payload.clear_tag,
        status=payload.status,
        owner_id=payload.owner_id,
        clear_owner=payload.clear_owner,
        organization_id=payload.organization_id,
        clear_organization=payload.clear_organization,
        location=payload.location,
        note=payload.note,
    )
    await session.commit()
    return AssetResponse.of(view)


@assets_router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, asset_id: UUID
) -> None:
    """지운다. **티켓에 이어져 있으면 거절한다** — 이력을 지우는 길은 없다."""
    await AssetService(session, permissions).delete(actor, asset_id)
    await session.commit()


__all__ = ["assets_router"]
