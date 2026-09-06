"""wiki 라우터. 스페이스와 문서."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.wiki.models import SPACE_KINDS
from ieum.modules.wiki.service import NewPage, PageService, PageView, SpaceService

spaces_router = APIRouter(prefix="/spaces", tags=["wiki"])
pages_router = APIRouter(prefix="/pages", tags=["wiki"])

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _parse_if_match(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    return int(value) if value.isdigit() else None


# ── 스키마 ──────────────────────────────────────────────────────

SpaceKind = Literal["team", "personal", "kb"]
assert set(SPACE_KINDS) == {"team", "personal", "kb"}


class SpaceCreateRequest(BaseModel):
    key: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    kind: SpaceKind = "team"


class SpaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    home_page_id: UUID | None = None
    #: null 을 "값 없음" 과 구분할 방법이 JSON 에 없다. 지우려면 이 플래그를 쓴다.
    clear_home_page: bool = False


class SpaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    description: str | None
    kind: str
    home_page_id: UUID | None
    archived_at: datetime | None


class SpacePageResponse(BaseModel):
    items: list[SpaceResponse]
    next_cursor: str | None = None
    total: int | None = None


class PageCreateRequest(BaseModel):
    space_id: UUID
    title: str = Field(min_length=1, max_length=500)
    parent_id: UUID | None = None
    body: str = Field(default="", max_length=1_000_000)
    front_matter: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list, max_length=30)
    publish: bool = False


class PageUpdateRequest(BaseModel):
    """부분 수정. 미포함이면 건드리지 않는다."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=1_000_000)
    front_matter: dict[str, Any] | None = None
    labels: list[str] | None = Field(default=None, max_length=30)
    #: 변경 요약. 커밋 메시지에 해당한다.
    message: str | None = Field(default=None, max_length=500)
    publish: bool | None = None


class PageMoveRequest(BaseModel):
    #: null 이면 최상위로 올린다.
    new_parent_id: UUID | None = None
    position: int | None = Field(default=None, ge=0)


class RestrictionPrincipal(BaseModel):
    kind: Literal["user", "group"]
    id: UUID


class RestrictionRequest(BaseModel):
    mode: Literal["view", "edit"]
    #: 빈 목록이면 제한 해제.
    principals: list[RestrictionPrincipal] = Field(default_factory=list, max_length=100)


class RestrictionResponse(BaseModel):
    mode: str
    principal_kind: str
    principal_id: UUID


class PageResponse(BaseModel):
    id: UUID
    space_id: UUID
    space_key: str
    parent_id: UUID | None
    path: str
    slug: str
    title: str
    status: str
    position: int
    version: int
    labels: list[str]
    body: str
    front_matter: dict[str, Any]
    #: 지금 보고 있는 판. 초안만 있으면 None.
    version_number: int | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PageNodeResponse(BaseModel):
    """트리의 한 칸. 본문은 싣지 않는다 — 트리에 본문이 실리면 무거워진다."""

    id: UUID
    parent_id: UUID | None
    path: str
    slug: str
    title: str
    status: str
    position: int


class PageVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    number: int
    title: str
    author_id: UUID | None
    message: str | None
    created_at: datetime


class PageVersionDetailResponse(PageVersionResponse):
    body: str
    front_matter: dict[str, Any]


def _page(view: PageView) -> PageResponse:
    page = view.page
    return PageResponse(
        id=page.id,
        space_id=page.space_id,
        space_key=view.space_key,
        parent_id=page.parent_id,
        path=page.path,
        slug=page.slug,
        title=page.title,
        status=page.status,
        position=page.position,
        version=page.version,
        labels=view.labels,
        body=view.body,
        front_matter=view.current.front_matter if view.current else {},
        version_number=view.current.number if view.current else None,
        archived_at=page.archived_at,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


# ── 스페이스 ────────────────────────────────────────────────────


@spaces_router.post("", response_model=SpaceResponse, status_code=status.HTTP_201_CREATED)
async def create_space(
    body: SpaceCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> SpaceResponse:
    space = await SpaceService(session, permissions).create(
        actor, key=body.key, name=body.name, description=body.description, kind=body.kind
    )
    await session.commit()
    return SpaceResponse.model_validate(space)


@spaces_router.get("", response_model=SpacePageResponse)
async def list_spaces(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    include_archived: bool = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> SpacePageResponse:
    """보이는 것만 돌려준다. 권한 없는 스페이스는 SQL 단계에서 걸러진다."""
    page = await SpaceService(session, permissions).list_for(
        actor,
        PageRequest(limit=limit, cursor=cursor),
        include_archived=include_archived,
        query=q,
    )
    return SpacePageResponse(
        items=[SpaceResponse.model_validate(s) for s in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@spaces_router.get("/by-key/{key}", response_model=SpaceResponse)
async def get_space_by_key(
    key: str, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> SpaceResponse:
    return SpaceResponse.model_validate(
        await SpaceService(session, permissions).get_by_key(actor, key)
    )


@spaces_router.get("/{space_id}", response_model=SpaceResponse)
async def get_space(
    space_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> SpaceResponse:
    return SpaceResponse.model_validate(
        await SpaceService(session, permissions).get(actor, space_id)
    )


@spaces_router.patch("/{space_id}", response_model=SpaceResponse)
async def update_space(
    space_id: UUID,
    body: SpaceUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> SpaceResponse:
    space = await SpaceService(session, permissions).update(
        actor,
        space_id,
        name=body.name,
        description=body.description,
        home_page_id=body.home_page_id,
        clear_home_page=body.clear_home_page,
    )
    await session.commit()
    return SpaceResponse.model_validate(space)


@spaces_router.post("/{space_id}/archive", response_model=SpaceResponse)
async def archive_space(
    space_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> SpaceResponse:
    space = await SpaceService(session, permissions).archive(actor, space_id)
    await session.commit()
    return SpaceResponse.model_validate(space)


@spaces_router.get("/{space_id}/tree", response_model=list[PageNodeResponse])
async def space_tree(
    space_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[PageNodeResponse]:
    """문서 트리. **볼 수 있는 것만** 온다 — 제목만 새어도 정보다."""
    rows = await PageService(session, permissions).tree(actor, space_id)
    return [
        PageNodeResponse(
            id=p.id,
            parent_id=p.parent_id,
            path=p.path,
            slug=p.slug,
            title=p.title,
            status=p.status,
            position=p.position,
        )
        for p in rows
    ]


# ── 문서 ────────────────────────────────────────────────────────


@pages_router.post("", response_model=PageResponse, status_code=status.HTTP_201_CREATED)
async def create_page(
    body: PageCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> PageResponse:
    view = await PageService(session, permissions).create(
        actor,
        NewPage(
            space_id=body.space_id,
            title=body.title,
            parent_id=body.parent_id,
            body=body.body,
            front_matter=body.front_matter,
            labels=body.labels,
            publish=body.publish,
        ),
    )
    await session.commit()
    return _page(view)


@pages_router.get("/by-path", response_model=PageResponse)
async def get_page_by_path(
    space: Annotated[str, Query(max_length=16)],
    path: Annotated[str, Query(max_length=2000)],
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> PageResponse:
    """`?space=ENG&path=deploy/rollback` 으로 연다. 사람이 주고받는 주소다."""
    return _page(await PageService(session, permissions).get_by_path(actor, space, path))


@pages_router.get("/{page_id}", response_model=PageResponse)
async def get_page(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> PageResponse:
    return _page(await PageService(session, permissions).get(actor, page_id))


@pages_router.patch("/{page_id}", response_model=PageResponse)
async def update_page(
    page_id: UUID,
    body: PageUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    if_match: IfMatch = None,
) -> PageResponse:
    view = await PageService(session, permissions).update(
        actor,
        page_id,
        title=body.title,
        body=body.body,
        front_matter=body.front_matter,
        labels=body.labels,
        message=body.message,
        publish=body.publish,
        expected_version=_parse_if_match(if_match),
    )
    await session.commit()
    return _page(view)


@pages_router.post("/{page_id}/move", response_model=PageResponse)
async def move_page(
    page_id: UUID,
    body: PageMoveRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> PageResponse:
    view = await PageService(session, permissions).move(
        actor, page_id, new_parent_id=body.new_parent_id, position=body.position
    )
    await session.commit()
    return _page(view)


@pages_router.post("/{page_id}/archive", response_model=PageResponse)
async def archive_page(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> PageResponse:
    view = await PageService(session, permissions).archive(actor, page_id)
    await session.commit()
    return _page(view)


@pages_router.get("/{page_id}/versions", response_model=list[PageVersionResponse])
async def page_history(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[PageVersionResponse]:
    rows = await PageService(session, permissions).history(actor, page_id)
    return [PageVersionResponse.model_validate(r) for r in rows]


@pages_router.get("/{page_id}/versions/{number}", response_model=PageVersionDetailResponse)
async def page_version(
    page_id: UUID,
    number: int,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> PageVersionDetailResponse:
    row = await PageService(session, permissions).version(actor, page_id, number)
    return PageVersionDetailResponse.model_validate(row)


@pages_router.post("/{page_id}/versions/{number}/restore", response_model=PageResponse)
async def restore_version(
    page_id: UUID,
    number: int,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> PageResponse:
    """옛 판의 내용으로 **새 판**을 만든다. 이력은 지우지 않는다."""
    view = await PageService(session, permissions).restore(actor, page_id, number)
    await session.commit()
    return _page(view)


@pages_router.get("/{page_id}/restrictions", response_model=list[RestrictionResponse])
async def page_restrictions(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[RestrictionResponse]:
    rows = await PageService(session, permissions).restrictions(actor, page_id)
    return [
        RestrictionResponse(
            mode=r.mode, principal_kind=r.principal_kind, principal_id=r.principal_id
        )
        for r in rows
    ]


@pages_router.put("/{page_id}/restrictions", response_model=list[RestrictionResponse])
async def set_page_restrictions(
    page_id: UUID,
    body: RestrictionRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[RestrictionResponse]:
    """해당 모드의 제한을 통째로 바꾼다. PUT 인 이유다 — 부분 수정이 아니다."""
    rows = await PageService(session, permissions).set_restrictions(
        actor,
        page_id,
        mode=body.mode,
        principals=[(p.kind, p.id) for p in body.principals],
    )
    await session.commit()
    return [
        RestrictionResponse(
            mode=r.mode, principal_kind=r.principal_kind, principal_id=r.principal_id
        )
        for r in rows
    ]


__all__ = ["pages_router", "spaces_router"]
