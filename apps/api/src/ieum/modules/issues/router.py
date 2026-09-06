"""issues HTTP 라우터. 얇게 유지한다."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.issues.bulk import BulkService
from ieum.modules.issues.models import Issue
from ieum.modules.issues.repository import HistoryRepository
from ieum.modules.issues.schemas import (
    BulkEditRequest,
    BulkEditResponse,
    BulkFailureResponse,
    CommentCreateRequest,
    CommentResponse,
    CommentUpdateRequest,
    FieldDefinitionResponse,
    HistoryEntryResponse,
    IssueCreateRequest,
    IssuePageResponse,
    IssueRelationsResponse,
    IssueResponse,
    IssueSummaryResponse,
    IssueTypeResponse,
    IssueUpdateRequest,
    LinkRequest,
    RelatedIssueResponse,
    TimeSummaryResponse,
    TransitionRequest,
    TransitionResponse,
    VersionResponse,
    WorkflowStateResponse,
    WorklogCreateRequest,
    WorklogPanelResponse,
    WorklogResponse,
    WorklogUpdateRequest,
)
from ieum.modules.issues.service import (
    CommentService,
    IssueService,
    IssueView,
    NewIssue,
)
from ieum.modules.issues.worklog import TimeSummary, WorklogService

issues_router = APIRouter(prefix="/issues", tags=["issues"])

#: 낙관적 잠금. 없으면 검사하지 않는다 (강제하면 단순 스크립트가 불편해진다).
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _parse_if_match(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    return int(value) if value.isdigit() else None


async def summary_rows(service: IssueService, issues: list[Issue]) -> list[IssueSummaryResponse]:
    """목록 응답 행. key·상태 이름을 배치 조회로 채운다.

    search_router 도 이걸 쓴다 — 두 목록이 서로 다른 모양을 내보내면
    프론트가 화면마다 분기해야 한다.
    """
    return [
        IssueSummaryResponse(
            id=row.issue.id,
            key=row.key,
            key_seq=row.issue.key_seq,
            project_id=row.issue.project_id,
            summary=row.issue.summary,
            state_id=row.issue.state_id,
            state_name=row.state_name,
            state_category=row.state_category,
            assignee_id=row.issue.assignee_id,
            priority=row.issue.priority,
            due_date=row.issue.due_date,
            updated_at=row.issue.updated_at,
        )
        for row in await service.to_summaries(issues)
    ]


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
        start_date=issue.start_date,
        due_date=issue.due_date,
        estimate_minutes=issue.estimate_minutes,
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
    service = IssueService(session, permissions)
    page = await service.list_for(
        actor,
        PageRequest(limit=limit, cursor=cursor),
        project_id=project_id,
        include_archived=include_archived,
    )
    return IssuePageResponse(
        items=await summary_rows(service, page.items),
        next_cursor=page.next_cursor,
        total=page.total,
    )


@issues_router.get("/types", response_model=list[IssueTypeResponse])
async def list_issue_types(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[IssueTypeResponse]:
    """생성 폼의 유형 선택지."""
    rows = await IssueService(session, permissions).list_types(actor, project_id)
    return [IssueTypeResponse.model_validate(r) for r in rows]


@issues_router.get("/fields", response_model=list[FieldDefinitionResponse])
async def list_field_definitions(
    project_id: UUID,
    type_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[FieldDefinitionResponse]:
    """이 프로젝트·유형에 뜨는 커스텀 필드."""
    rows = await IssueService(session, permissions).list_field_definitions(
        actor, project_id, type_id
    )
    return [FieldDefinitionResponse.model_validate(r) for r in rows]


@issues_router.get("/versions", response_model=list[VersionResponse])
async def list_versions(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[VersionResponse]:
    """version 종류 커스텀 필드가 고를 수 있는 버전 목록."""
    rows = await IssueService(session, permissions).list_versions(actor, project_id)
    return [VersionResponse.model_validate(r) for r in rows]


@issues_router.get("/states", response_model=list[WorkflowStateResponse])
async def list_workflow_states(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[WorkflowStateResponse]:
    """필터 칩과 보드 컬럼 편집기가 쓰는 상태 목록."""
    rows = await IssueService(session, permissions).list_workflow_states(actor, project_id)
    return [WorkflowStateResponse.model_validate(r) for r in rows]


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


# ── 시간 추적 ───────────────────────────────────────────────────


def _summary(summary: TimeSummary) -> TimeSummaryResponse:
    return TimeSummaryResponse(
        estimate_minutes=summary.estimate_minutes,
        spent_minutes=summary.spent_minutes,
        remaining_minutes=summary.remaining_minutes,
        over_estimate=summary.over_estimate,
    )


@issues_router.get("/{issue_id}/worklogs", response_model=WorklogPanelResponse)
async def list_worklogs(
    issue_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> WorklogPanelResponse:
    service = WorklogService(session, permissions)
    return WorklogPanelResponse(
        summary=_summary(await service.summary(actor, issue_id)),
        items=[WorklogResponse.model_validate(r) for r in await service.list_for(actor, issue_id)],
    )


@issues_router.post(
    "/{issue_id}/worklogs",
    response_model=WorklogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_worklog(
    issue_id: UUID,
    body: WorklogCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> WorklogResponse:
    row = await WorklogService(session, permissions).add(
        actor,
        issue_id,
        spent_minutes=body.spent_minutes,
        work_date=body.work_date,
        comment=body.comment,
    )
    await session.commit()
    return WorklogResponse.model_validate(row)


@issues_router.patch("/worklogs/{worklog_id}", response_model=WorklogResponse)
async def update_worklog(
    worklog_id: UUID,
    body: WorklogUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> WorklogResponse:
    row = await WorklogService(session, permissions).update(
        actor,
        worklog_id,
        spent_minutes=body.spent_minutes,
        work_date=body.work_date,
        comment=body.comment,
        clear_comment=body.clear_comment,
    )
    await session.commit()
    return WorklogResponse.model_validate(row)


@issues_router.delete("/worklogs/{worklog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worklog(
    worklog_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await WorklogService(session, permissions).delete(actor, worklog_id)
    await session.commit()


# ── 관계 ────────────────────────────────────────────────────────


@issues_router.get("/{issue_id}/relations", response_model=IssueRelationsResponse)
async def list_relations(
    issue_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> IssueRelationsResponse:
    """부모·자식·링크를 한 번에. 상세 화면의 관계 패널이 쓴다."""
    service = IssueService(session, permissions)
    relations = await service.relations(actor, issue_id)

    # 모든 관련 이슈의 key·상태를 한 번에 채운다.
    everything = [
        *([relations.parent] if relations.parent is not None else []),
        *relations.children,
        *[link.issue for link in relations.links],
    ]
    rows = {row.id: row for row in await summary_rows(service, everything)}

    return IssueRelationsResponse(
        parent=None if relations.parent is None else rows[relations.parent.id],
        children=[rows[c.id] for c in relations.children],
        links=[
            RelatedIssueResponse(
                link_id=link.link_id,
                kind=link.kind,
                outward=link.outward,
                issue=rows[link.issue.id],
            )
            for link in relations.links
        ],
    )


@issues_router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_issue(
    link_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await IssueService(session, permissions).unlink(actor, link_id)
    await session.commit()


# ── 일괄 편집 ───────────────────────────────────────────────────


@issues_router.post("/bulk", response_model=BulkEditResponse)
async def bulk_edit(
    body: BulkEditRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> BulkEditResponse:
    """고른 이슈를 한 번에 바꾼다.

    200 으로 돌려주되 건별 결과를 담는다. 하나가 실패했다고 전체를 4xx 로
    내면 성공한 것까지 실패로 보이고, 조용히 넘기면 무엇이 안 됐는지 모른다.
    """
    result = await BulkService(session, permissions).edit(
        actor,
        body.issue_ids,
        changes=body.changes,
        add_labels=body.add_labels,
        remove_labels=body.remove_labels,
        transition_id=body.transition_id,
    )
    await session.commit()
    return BulkEditResponse(
        updated=result.updated,
        failed=[
            BulkFailureResponse(issue_id=f.issue_id, code=f.code, message=f.message)
            for f in result.failed
        ],
    )
