"""wiki 라우터. 스페이스와 문서."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Header, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.exceptions import ValidationError
from ieum.core.markdown import MAX_LENGTH as MAX_BODY_LENGTH
from ieum.core.markdown.anchors import MAX_CONTEXT, MAX_QUOTE, Anchor
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.wiki.models import SPACE_KINDS
from ieum.modules.wiki.portable import MAX_ARCHIVE_BYTES, content_disposition
from ieum.modules.wiki.service import (
    CommentView,
    NewPage,
    PageCommentService,
    PageService,
    PageTemplateService,
    PageView,
    SpaceService,
)

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
    body: str = Field(default="", max_length=MAX_BODY_LENGTH)
    front_matter: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list, max_length=30)
    publish: bool = False


class PageUpdateRequest(BaseModel):
    """부분 수정. 미포함이면 건드리지 않는다."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=MAX_BODY_LENGTH)
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


# ── 임포트·내보내기 ─────────────────────────────────────────────


@spaces_router.post("/{space_id}/import", response_model=list[PageResponse])
async def import_into_space(
    space_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    file: Annotated[UploadFile, File()],
    parent_id: UUID | None = None,
) -> list[PageResponse]:
    """`.md` 하나 또는 `.md` 를 담은 ZIP 을 올린다.

    스토리지를 거치지 않는다 — 첨부와 달리 내용을 **서버가 읽어야** 하고,
    묶음 크기가 제한돼 있어 요청 하나로 끝난다.
    """
    raw = await file.read()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "묶음이 너무 크다.",
            code="wiki.import_too_large",
            details={"max": MAX_ARCHIVE_BYTES},
        )
    service = PageService(session, permissions)
    name = file.filename or "upload.md"

    if raw[:2] == b"PK":
        views = await service.import_archive(
            actor, space_id=space_id, data=raw, parent_id=parent_id
        )
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            # 추측해서 열면 깨진 글자가 문서로 들어앉고 되돌릴 방법이 없다.
            raise ValidationError(
                "UTF-8 로 저장된 파일만 읽을 수 있다.", code="wiki.invalid_encoding"
            ) from exc
        views = [
            await service.import_markdown(
                actor, space_id=space_id, filename=name, content=text, parent_id=parent_id
            )
        ]

    await session.commit()
    return [_page(v) for v in views]


@spaces_router.get("/{space_id}/export")
async def export_space(
    space_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> Response:
    """스페이스를 ZIP 으로. 문서 경로가 그대로 폴더 구조가 된다."""
    data = await PageService(session, permissions).export_space(actor, space_id)
    space = await SpaceService(session, permissions).get(actor, space_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(f"{space.key}.zip"),
            # 내보내기는 사용자별 ACL 을 탄다. 중간 캐시에 남으면 안 된다.
            "Cache-Control": "no-store",
        },
    )


@pages_router.get("/{page_id}/export")
async def export_page(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> Response:
    filename, content = await PageService(session, permissions).export_markdown(actor, page_id)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


# ── 코멘트 ──────────────────────────────────────────────────────


class AnchorPayload(BaseModel):
    """인용으로 위치를 잡는다 (wiki-markdown.md 6절).

    **평문 기준**이다. 사람은 렌더된 글을 드래그하지 `**굵게**` 같은 원문을
    고르지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    exact: str = Field(min_length=1, max_length=MAX_QUOTE)
    prefix: str = Field(default="", max_length=MAX_CONTEXT)
    suffix: str = Field(default="", max_length=MAX_CONTEXT)
    #: 같은 인용이 여러 번 나올 때 몇 번째인지. 1부터.
    occurrence: int = Field(default=1, ge=1, le=1000)
    #: 달 때의 판. 진단용이라 앵커링에는 쓰지 않는다.
    version_number: int | None = None


class CommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_BODY_LENGTH)
    #: 없으면 문서 전체에 다는 코멘트다.
    anchor: AnchorPayload | None = None
    parent_id: UUID | None = None


class CommentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_BODY_LENGTH)


class CommentResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = True


class CommentAnchorResponse(BaseModel):
    exact: str
    prefix: str
    suffix: str
    occurrence: int
    version_number: int | None


class CommentMatchResponse(BaseModel):
    """지금 문서에서 인용이 붙은 자리. 평문 기준 오프셋."""

    start: int
    end: int
    how: str
    score: float
    #: 지금 문서에 실제로 있는 글자. 퍼지로 붙었으면 인용문과 다르다.
    found: str


class CommentResponse(BaseModel):
    id: UUID
    page_id: UUID
    author_id: UUID | None
    body: str
    parent_id: UUID | None
    anchor: CommentAnchorResponse | None
    #: 못 붙었으면 `orphaned`. 인용문은 그대로 남는다 — 조용히 지우지 않는다.
    anchor_status: str
    match: CommentMatchResponse | None
    resolved_at: datetime | None
    edited_at: datetime | None
    created_at: datetime


def _comment(view: CommentView) -> CommentResponse:
    row = view.comment
    return CommentResponse(
        id=row.id,
        page_id=row.page_id,
        author_id=row.author_id,
        body=row.body,
        parent_id=row.parent_id,
        anchor=CommentAnchorResponse(**Anchor.from_json(row.anchor).as_json())
        if row.anchor
        else None,
        anchor_status=row.anchor_status,
        match=CommentMatchResponse(
            start=view.match.start,
            end=view.match.end,
            how=view.match.how,
            score=round(view.match.score, 3),
            found=view.match.found,
        )
        if view.match
        else None,
        resolved_at=row.resolved_at,
        edited_at=row.edited_at,
        created_at=row.created_at,
    )


@pages_router.get("/{page_id}/comments", response_model=list[CommentResponse])
async def list_page_comments(
    page_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[CommentResponse]:
    views = await PageCommentService(session, permissions).list_for(actor, page_id)
    # 읽을 때마다 다시 앵커링한다. 상태가 바뀌었으면 그걸 남긴다.
    await session.commit()
    return [_comment(v) for v in views]


@pages_router.post(
    "/{page_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED
)
async def add_page_comment(
    page_id: UUID,
    body: CommentCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> CommentResponse:
    view = await PageCommentService(session, permissions).add(
        actor,
        page_id,
        body=body.body,
        anchor=body.anchor.model_dump() if body.anchor else None,
        parent_id=body.parent_id,
    )
    await session.commit()
    return _comment(view)


@pages_router.patch("/comments/{comment_id}", response_model=CommentResponse)
async def edit_page_comment(
    comment_id: UUID,
    body: CommentUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> CommentResponse:
    view = await PageCommentService(session, permissions).edit(actor, comment_id, body.body)
    await session.commit()
    return _comment(view)


@pages_router.post("/comments/{comment_id}/resolve", response_model=CommentResponse)
async def resolve_page_comment(
    comment_id: UUID,
    body: CommentResolveRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> CommentResponse:
    view = await PageCommentService(session, permissions).resolve(
        actor, comment_id, resolved=body.resolved
    )
    await session.commit()
    return _comment(view)


@pages_router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page_comment(
    comment_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> None:
    await PageCommentService(session, permissions).delete(actor, comment_id)
    await session.commit()


# ── 이슈 링크 ───────────────────────────────────────────────────


class LinkedPageResponse(BaseModel):
    """이 이슈를 언급한 문서 하나."""

    id: UUID
    space_id: UUID
    space_key: str
    path: str
    title: str


@pages_router.get("/mentioning/{issue_id}", response_model=list[LinkedPageResponse])
async def pages_mentioning_issue(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[LinkedPageResponse]:
    """이 이슈를 본문에서 참조한 문서들.

    위키가 답한다. 이슈 모듈이 답하려면 위키를 알아야 하고, 그러면 의존
    그래프에 고리가 생긴다 (overview.md 모듈 의존 그래프).
    """
    service = PageService(session, permissions)
    pages = await service.pages_mentioning(actor, issue_id)
    spaces = SpaceService(session, permissions)
    out: list[LinkedPageResponse] = []
    for page in pages:
        space = await spaces.get(actor, page.space_id)
        out.append(
            LinkedPageResponse(
                id=page.id,
                space_id=page.space_id,
                space_key=space.key,
                path=page.path,
                title=page.title,
            )
        )
    return out


# ── 템플릿 ──────────────────────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=MAX_BODY_LENGTH)
    category: str | None = Field(default=None, max_length=100)
    #: 없으면 전역 템플릿. 전역은 스페이스 생성 권한이 있어야 만든다.
    space_id: UUID | None = None


class TemplateResponse(BaseModel):
    id: UUID
    space_id: UUID | None
    name: str
    body: str
    category: str | None


@spaces_router.get("/{space_id}/templates", response_model=list[TemplateResponse])
async def list_templates(
    space_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[TemplateResponse]:
    """그 스페이스 것 + 전역."""
    rows = await PageTemplateService(session, permissions).list_for(actor, space_id)
    return [TemplateResponse.model_validate(r, from_attributes=True) for r in rows]


@spaces_router.post(
    "/{space_id}/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_template(
    space_id: UUID,
    body: TemplateCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> TemplateResponse:
    template = await PageTemplateService(session, permissions).create(
        actor,
        space_id=space_id,
        name=body.name,
        body=body.body,
        category=body.category,
    )
    await session.commit()
    return TemplateResponse.model_validate(template, from_attributes=True)


@spaces_router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> None:
    await PageTemplateService(session, permissions).delete(actor, template_id)
    await session.commit()


# ── 판 비교 ─────────────────────────────────────────────────────


class DiffLineResponse(BaseModel):
    op: str
    old_number: int | None
    new_number: int | None
    text: str


class DiffResponse(BaseModel):
    page_id: UUID
    before: int
    after: int
    before_created_at: datetime
    after_created_at: datetime
    lines: list[DiffLineResponse]
    added: int
    removed: int
    #: 상한에 걸려 잘렸으면 True. 화면이 "여기까지"라고 말해야 한다.
    truncated: bool


@pages_router.get("/{page_id}/diff", response_model=DiffResponse)
async def compare_versions(
    page_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    before: Annotated[int, Query(ge=1)],
    after: Annotated[int, Query(ge=1)],
) -> DiffResponse:
    """판 사이 라인 diff (wiki-markdown.md 9절)."""
    old, new, result = await PageService(session, permissions).compare(
        actor, page_id, before=before, after=after
    )
    return DiffResponse(
        page_id=page_id,
        before=old.number,
        after=new.number,
        before_created_at=old.created_at,
        after_created_at=new.created_at,
        lines=[
            DiffLineResponse(
                op=line.op,
                old_number=line.old_number,
                new_number=line.new_number,
                text=line.text,
            )
            for line in result.lines
        ],
        added=result.added,
        removed=result.removed,
        truncated=result.truncated,
    )


__all__ = ["pages_router", "spaces_router"]
