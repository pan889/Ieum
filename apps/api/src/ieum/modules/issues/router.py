"""issues HTTP 라우터. 얇게 유지한다."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.issues.repository import HistoryRepository
from ieum.modules.issues.schemas import (
    CommentCreateRequest,
    CommentResponse,
    CommentUpdateRequest,
    HistoryEntryResponse,
    IssueCreateRequest,
    IssuePageResponse,
    IssueResponse,
    IssueSummaryResponse,
    IssueUpdateRequest,
    LinkRequest,
    TransitionRequest,
    TransitionResponse,
)
from ieum.modules.issues.service import (
    CommentService,
    IssueService,
    IssueView,
    NewIssue,
)

issues_router = APIRouter(prefix="/issues", tags=["issues"])

#: 낙관적 잠금. 없으면 검사하지 않는다 (강제하면 단순 스크립트가 불편해진다).
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _parse_if_match(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    return int(value) if value.isdigit() else None


def _to_response(view: IssueView) -> IssueResponse:
    issue = view.issue
    return IssueResponse(
        id=issue.id,
        key=view.key,
        project_id=issue.project_id,
        summary=issue.summary,
        description=issue.description,
        type_id=issue.type_id,
        type_name=view.type_name,
        state_id=issue.state_id,
        state_name=view.state_name,
        state_category=view.state_category,
        reporter_id=issue.reporter_id,
        assignee_id=issue.assignee_id,
        priority=issue.priority,
        parent_id=issue.parent_id,
        due_date=issue.due_date,
        progress=issue.progress,
        resolved_at=issue.resolved_at,
        archived_at=issue.archived_at,
        version=issue.version,
        labels=view.labels,
        custom_fields=view.custom_fields,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


@issues_router.post("", response_model=IssueResponse, status_code=status.HTTP_201_CREATED)
async def create_issue(
    body: IssueCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> IssueResponse:
    view = await IssueService(session, permissions).create(
        actor,
        NewIssue(
            project_id=body.project_id,
            summary=body.summary,
            type_id=body.type_id,
            description=body.description,
            assignee_id=body.assignee_id,
            priority=body.priority,
            parent_id=body.parent_id,
            due_date=body.due_date,
            labels=body.labels,
            custom_fields=body.custom_fields,
        ),
    )
    await session.commit()
    return _to_response(view)


@issues_router.get("", response_model=IssuePageResponse)
async def list_issues(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    project_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    include_archived: bool = False,
) -> IssuePageResponse:
    page = await IssueService(session, permissions).list_for(
        actor,
        PageRequest(limit=limit, cursor=cursor),
        project_id=project_id,
        include_archived=include_archived,
    )
    return IssuePageResponse(
        items=[IssueSummaryResponse.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@issues_router.get("/by-key/{key}", response_model=IssueResponse)
async def get_issue_by_key(
    key: str, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> IssueResponse:
    """`PROJ-123` 으로 조회. 사람이 주고받는 식별자다."""
    return _to_response(await IssueService(session, permissions).get_by_key(actor, key))


@issues_router.get("/{issue_id}", response_model=IssueResponse)
async def get_issue(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> IssueResponse:
    return _to_response(await IssueService(session, permissions).get(actor, issue_id))


@issues_router.patch("/{issue_id}", response_model=IssueResponse)
async def update_issue(
    issue_id: UUID,
    body: IssueUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    if_match: IfMatch = None,
) -> IssueResponse:
    view = await IssueService(session, permissions).update(
        actor,
        issue_id,
        body.changes,
        expected_version=_parse_if_match(if_match),
        labels=body.labels,
        custom_fields=body.custom_fields,
    )
    await session.commit()
    return _to_response(view)


@issues_router.post("/{issue_id}/archive", response_model=IssueResponse)
async def archive_issue(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> IssueResponse:
    service = IssueService(session, permissions)
    await service.archive(actor, issue_id)
    view = await service.get(actor, issue_id)
    await session.commit()
    return _to_response(view)


@issues_router.get("/{issue_id}/transitions", response_model=list[TransitionResponse])
async def list_transitions(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[TransitionResponse]:
    """가능한 전이와 막힌 이유. UI 가 버튼을 비활성화하고 사유를 보여준다."""
    available = await IssueService(session, permissions).available_transitions(actor, issue_id)
    return [
        TransitionResponse(
            id=t.id,
            name=t.name,
            to_state_id=t.to_state_id,
            to_state_name=t.to_state_name,
            blocked_by=t.blocked_by,
        )
        for t in available
    ]


@issues_router.post("/{issue_id}/transition", response_model=IssueResponse)
async def transition_issue(
    issue_id: UUID,
    body: TransitionRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    if_match: IfMatch = None,
) -> IssueResponse:
    view = await IssueService(session, permissions).transition(
        actor,
        issue_id,
        body.transition_id,
        inputs=body.inputs,
        expected_version=_parse_if_match(if_match),
    )
    await session.commit()
    return _to_response(view)


@issues_router.post("/{issue_id}/links", status_code=status.HTTP_204_NO_CONTENT)
async def link_issue(
    issue_id: UUID,
    body: LinkRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await IssueService(session, permissions).link(actor, issue_id, body.to_issue_id, body.kind)
    await session.commit()


@issues_router.get("/{issue_id}/history", response_model=list[HistoryEntryResponse])
async def list_history(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[HistoryEntryResponse]:
    # 이력을 보려면 이슈를 볼 수 있어야 한다. get() 이 권한을 검사한다.
    await IssueService(session, permissions).get(actor, issue_id)
    rows = await HistoryRepository(session).for_issue(issue_id)
    return [HistoryEntryResponse.model_validate(r) for r in rows]


# ── 코멘트 ──────────────────────────────────────────────────────


@issues_router.get("/{issue_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    issue_id: UUID, actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[CommentResponse]:
    """내부 노트는 권한이 있을 때만 포함된다."""
    rows = await CommentService(session, permissions).list_for(actor, issue_id)
    return [CommentResponse.model_validate(r) for r in rows]


@issues_router.post(
    "/{issue_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    issue_id: UUID,
    body: CommentCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> CommentResponse:
    comment = await CommentService(session, permissions).add(
        actor, issue_id, body.body, is_internal=body.is_internal
    )
    await session.commit()
    return CommentResponse.model_validate(comment)


@issues_router.patch("/comments/{comment_id}", response_model=CommentResponse)
async def edit_comment(
    comment_id: UUID,
    body: CommentUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> CommentResponse:
    comment = await CommentService(session, permissions).edit(actor, comment_id, body.body)
    await session.commit()
    return CommentResponse.model_validate(comment)
