"""issues 의 공개 인터페이스.

`desk` 는 이슈 위에 얹히고(티켓 = 이슈), `wiki` 는 entity_link 로 참조한다.
둘 다 이 파일만 import 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, get_permission_service
from ieum.modules.issues.models import Issue
from ieum.modules.issues.repository import IssueRepository, WorkflowRepository
from ieum.modules.org import contracts as org

if TYPE_CHECKING:
    from ieum.modules.issues.service import IssueView


@dataclass(frozen=True, slots=True)
class IssueRef:
    """다른 모듈이 이슈를 참조할 때 쓰는 최소 정보."""

    id: UUID
    project_id: UUID
    key_seq: int
    summary: str
    state_id: UUID
    state_category: str
    assignee_id: UUID | None
    is_archived: bool


async def get_issue(session: AsyncSession, issue_id: UUID) -> IssueRef | None:
    issue = await IssueRepository(session).get(issue_id)
    if issue is None:
        return None
    state = await WorkflowRepository(session).state(issue.state_id)
    return IssueRef(
        id=issue.id,
        project_id=issue.project_id,
        key_seq=issue.key_seq,
        summary=issue.summary,
        state_id=issue.state_id,
        state_category=state.category if state else "",
        assignee_id=issue.assignee_id,
        is_archived=issue.is_archived,
    )


async def is_resolved(session: AsyncSession, issue_id: UUID) -> bool:
    """완료 여부. 상태 '이름'이 아니라 category 로 판단한다.

    사용자가 상태 이름을 뭐라고 짓든 시스템은 category 를 본다 —
    SLA 클럭 정지·번다운 계산이 여기에 걸린다.
    """
    ref = await get_issue(session, issue_id)
    return ref is not None and ref.state_category == "done"


def issue_model() -> type[Issue]:
    """core 의 권한 가드 등록에 쓸 타입. 인스턴스를 노출하지 않는다."""
    return Issue


async def get_issue_by_key(session: AsyncSession, key: str) -> IssueRef | None:
    """`ENG-12` 로 찾는다. 본문의 `issue:ENG-12` 링크를 푸는 데 쓴다.

    키는 사람이 손으로 쓴 글자다. 모양이 틀리면 조용히 None 이다 — 문서
    저장을 막을 일이 아니다.
    """
    project_key, _, number = key.rpartition("-")
    if not project_key or not number.isdigit():
        return None
    project = await org.get_project_by_key(session, project_key.upper())
    if project is None:
        return None
    issue = await IssueRepository(session).get_by_key_seq(project.id, int(number))
    return await get_issue(session, issue.id) if issue else None


# ── 데스크(티켓) 계약 ───────────────────────────────────────────
#
# 티켓은 이슈다 (ADR-0003). `desk` 는 이슈 모델을 볼 수 없으므로, 티켓을
# 만들고 목록에 그리는 데 필요한 것만 여기서 내준다.


@dataclass(frozen=True, slots=True)
class TicketIssue:
    """포털 목록·상세 한 줄. 표시에 필요한 것만 담는다."""

    id: UUID
    project_id: UUID
    key: str
    summary: str
    description: str | None
    #: 상태 이름은 관리자가 지은 데이터다(번역하지 않는다). category 는
    #: 시스템 값이므로 화면이 그것으로 색·문구를 고른다.
    state_name: str
    state_category: str
    priority: int
    created_at: datetime
    updated_at: datetime
    custom_fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FieldDefinitionRef:
    """폼이 위젯을 고르는 데 필요한 정의. `description` 은 내주지 않는다 —
    한국어로 고정된 개발자용 메모다 (i18n.md 1절)."""

    key: str
    name: str
    kind: str
    config: dict[str, Any]
    is_required: bool
    position: int


async def get_issue_type(
    session: AsyncSession, *, project_id: UUID, issue_type_id: UUID
) -> str | None:
    """이 프로젝트에서 쓸 수 있는 유형이면 이름을, 아니면 None 을 돌려준다.

    "있는가" 가 아니라 "이 프로젝트에서 쓸 수 있는가" 를 본다. 다른 프로젝트
    전용 유형을 요청 유형에 걸어 두면 폼은 저장되고 제출은 실패한다.
    """
    from ieum.modules.issues.repository import IssueTypeRepository

    types = await IssueTypeRepository(session).available_for(project_id)
    return next((t.name for t in types if t.id == issue_type_id), None)


async def get_field_definitions(
    session: AsyncSession, *, project_id: UUID, issue_type_id: UUID
) -> list[FieldDefinitionRef]:
    """이 프로젝트·유형에 뜨는 커스텀 필드 정의. 요청 유형 폼이 쓴다."""
    from ieum.modules.issues.repository import FieldDefinitionRepository

    rows = await FieldDefinitionRepository(session).applicable_to(
        project_id=project_id, issue_type_id=issue_type_id
    )
    return [
        FieldDefinitionRef(
            key=row.key,
            name=row.name,
            kind=row.kind,
            config=dict(row.config),
            is_required=row.is_required,
            position=row.position,
        )
        for row in rows
    ]


async def create_ticket(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    *,
    project_id: UUID,
    issue_type_id: UUID,
    summary: str,
    description: str | None,
    custom_fields: dict[str, Any],
    anonymous: bool = False,
) -> TicketIssue:
    """**권한 검사 없이** 이슈를 만든다. 포털 제출 전용이다.

    `desk` 가 "이 포털이 이 요청 유형을 열어 두었는가" 를 이미 판단했다는
    전제다. 그래서 이름에 `ticket` 이 들어간다 — 범용 생성 통로가 아니다.

    `anonymous` 면 신고자를 남기지 않는다(게스트 요청). 주소는 `desk` 의
    `ticket_ext.guest_email` 이 갖는다 — 검증되지 않은 값이므로 이슈의
    신고자 자리에 올려 놓지 않는다.
    """
    from ieum.modules.issues.service import IssueService, NewIssue

    service = IssueService(session, permissions)
    view = await service.create_authorized(
        actor,
        NewIssue(
            project_id=project_id,
            type_id=issue_type_id,
            summary=summary,
            description=description,
            custom_fields=custom_fields,
        ),
        anonymous=anonymous,
    )
    return _ticket(view)


def _ticket(view: IssueView) -> TicketIssue:
    return TicketIssue(
        id=view.issue.id,
        project_id=view.issue.project_id,
        key=view.key,
        summary=view.issue.summary,
        description=view.issue.description,
        state_name=view.state_name,
        state_category=view.state_category,
        priority=view.issue.priority,
        created_at=view.issue.created_at,
        updated_at=view.issue.updated_at,
        custom_fields=dict(view.custom_fields),
    )


async def get_tickets(session: AsyncSession, issue_ids: Sequence[UUID]) -> dict[UUID, TicketIssue]:
    """여러 티켓을 한 번에. 포털 목록이 행마다 조회하지 않게 한다.

    **권한을 보지 않는다.** 목록의 범위는 `ticket_ext` 쪽에서 이미 좁혀졌고
    (자기 것 또는 자기 조직 것), 여기서 다시 이슈 권한을 보면 고객은 아무
    것도 못 본다.
    """
    if not issue_ids:
        return {}
    from ieum.modules.issues.service import IssueService

    # 권한 서비스가 필요 없는 경로다. 조회 전용 서비스를 만드는 대신
    # 리포지토리로 바로 간다 — `to_view` 가 권한을 보지 않기 때문이다.
    repo = IssueRepository(session)
    issues = await repo.get_many(list(dict.fromkeys(issue_ids)))
    if not issues:
        return {}
    service = IssueService(session, get_permission_service())
    return {issue.id: _ticket(await service.to_view(issue)) for issue in issues}


# ── 고객이 보는 대화 ───────────────────────────────────────────
#
# 내부 노트는 **고객에게 절대 나가지 않는다** (auth.md 5절). 그 보장을
# 구조로 만든다: 아래 두 함수에는 `include_internal` 매개변수가 **없다.**
# 매개변수로 두면 어딘가에서 True 가 흘러 들어오고, 그 한 번이 사고다.


@dataclass(frozen=True, slots=True)
class PublicComment:
    """고객이 볼 수 있는 코멘트 하나. `is_internal` 이 없다 — 언제나 False 다."""

    id: UUID
    author_id: UUID | None
    body: str
    created_at: datetime
    edited_at: datetime | None


async def list_public_comments(session: AsyncSession, issue_id: UUID) -> list[PublicComment]:
    """공개 코멘트만. **내부 노트는 SQL 단계에서 빠진다.**

    권한을 보지 않는다 — 이 티켓이 이 고객의 것인지는 `desk` 가 이미
    판단했고, 고객에게는 `issue.view` 가 없으므로 여기서 다시 보면 아무
    것도 못 본다.
    """
    from ieum.modules.issues.repository import CommentRepository

    rows = await CommentRepository(session).list_for_issue(issue_id, include_internal=False)
    return [
        PublicComment(
            id=row.id,
            author_id=row.author_id,
            body=row.body,
            created_at=row.created_at,
            edited_at=row.edited_at,
        )
        for row in rows
    ]


async def add_public_comment(
    session: AsyncSession, actor: Actor, issue_id: UUID, body: str
) -> PublicComment:
    """고객의 회신. **언제나 공개**이고 권한을 보지 않는다.

    `CommentService.add` 를 쓰지 않는 이유는 그쪽이 `issue.comment.add` 를
    요구하기 때문이다 — 고객에게 그 권한을 주면 포털 밖의 이슈에도 코멘트를
    달 수 있게 된다. `create_ticket` 과 같은 판단이다.

    알림은 나간다: 상담원이 회신을 못 보면 티켓이 멈춘다.
    """
    from ieum.core.markdown import normalize as normalize_markdown
    from ieum.core.outbox import publish
    from ieum.modules.issues import events as issue_events
    from ieum.modules.issues.models import IssueComment
    from ieum.modules.issues.repository import CommentRepository

    text = normalize_markdown(body)
    if not text:
        from ieum.core.exceptions import ValidationError

        raise ValidationError("내용을 비울 수 없다.", code="issues.comment_empty")

    issue = await IssueRepository(session).get(issue_id)
    if issue is None:
        from ieum.core.exceptions import NotFoundError

        raise NotFoundError("요청을 찾을 수 없다.")

    comment = CommentRepository(session).add(
        IssueComment(issue_id=issue_id, author_id=actor.user_id, body=text, is_internal=False)
    )
    await session.flush()

    project = await org.get_project(session, issue.project_id)
    publish(
        session,
        issue_events.IssueCommented(
            aggregate_id=issue.id,
            project_id=issue.project_id,
            issue_key=f"{project.key}-{issue.key_seq}" if project else "",
            summary=issue.summary,
            comment_id=comment.id,
            actor_id=actor.user_id,
            is_internal=False,
            assignee_id=issue.assignee_id,
            reporter_id=issue.reporter_id,
            # 고객의 회신에서 멘션을 풀지 않는다. 고객은 내부 사람의 이름을
            # 몰라야 하고, 본문에 `@` 를 적었다고 내부 사용자를 찾아 주면
            # 그 자체가 이름을 확인해 주는 통로가 된다.
            mentioned_ids=[],
        ),
    )
    return PublicComment(
        id=comment.id,
        author_id=comment.author_id,
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
    )


# ── 큐 (C3) ────────────────────────────────────────────────────
#
# 큐는 IQL 로 만든 티켓 목록이다. 질의를 실행하는 것은 `issues` 의 일이고
# (컴파일러·ACL·이슈 모델이 여기 있다), "티켓만" 이라는 조건은 `desk` 가
# 자기 테이블로 만들어 넘긴다.


@dataclass(frozen=True, slots=True)
class TicketRow:
    """큐 한 줄. 상세(`TicketIssue`)보다 가볍다.

    **행마다 조회하지 않는다.** 처음에는 `TicketIssue` 를 그대로 냈는데, 그건
    `to_view` 를 행마다 부르므로 라벨·커스텀 필드까지 한 줄에 다섯 번씩
    조회한다 — 50줄이면 백 번이 넘고, 큐는 상담원이 하루 종일 여는 화면이다.
    목록에 필요한 것만 담고 `to_summaries`(배치 조회)를 쓴다. 그 헬퍼는
    이슈 목록·검색이 이미 쓰고 있었다.
    """

    id: UUID
    project_id: UUID
    key: str
    summary: str
    state_name: str
    state_category: str
    priority: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IqlProblem:
    """질의가 틀렸을 때. 코드와 사람이 읽을 문구, 그리고 위치."""

    code: str
    message: str
    details: dict[str, Any]


async def validate_iql(
    session: AsyncSession, permissions: PermissionService, actor: Actor, iql: str
) -> IqlProblem | None:
    """문법·필드 검증. 실행하지 않는다. 맞으면 `None` 이다.

    **큐를 저장할 때 부른다.** 저장은 되고 실행이 실패하는 큐를 만들 수 없게
    하는 것이 요점이다 — 요청 유형 폼에서 같은 판단을 했다. 큐가 그런 상태로
    남으면 사이드바에 이름은 있는데 눌렀을 때만 실패하고, 그 사이 그 큐로
    들어와야 할 티켓들은 아무도 안 본다.
    """
    from ieum.modules.issues.search import SearchService

    result = await SearchService(session, permissions).validate(actor, iql)
    if result.valid:
        return None
    error = result.error or {}
    return IqlProblem(
        # 기본값은 실제 IQL 코드 접두사와 같아야 한다. 한 번 `issues.` 로
        # 적었고, 그러면 카탈로그에 없는 코드가 화면에 그대로 찍힌다.
        code=str(error.get("code", "iql.invalid")),
        message=str(error.get("message", "질의를 읽을 수 없다.")),
        details={k: v for k, v in error.items() if k not in ("code", "message")},
    )


async def run_iql(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    iql: str,
    request: PageRequest,
    *,
    extra_where: ColumnElement[bool] | None = None,
) -> Page[TicketRow]:
    """IQL 을 **실행자 권한으로** 돌린다.

    큐를 만든 사람의 권한을 승계하지 않는다. 승계하면 큐를 공유하는 것이 곧
    권한 상승이 된다(query-language.md 6절, 저장 필터와 같은 판단이다).

    `extra_where` 는 부르는 쪽의 조건을 한 질의에 넣는 자리다 — 밖에서
    걸러내면 페이지가 어긋난다.
    """
    from ieum.modules.issues.search import SearchService
    from ieum.modules.issues.service import IssueService

    page = await SearchService(session, permissions).search(
        actor, iql, request, extra_where=extra_where
    )
    service = IssueService(session, permissions)
    return Page(
        items=[
            TicketRow(
                id=row.issue.id,
                project_id=row.issue.project_id,
                key=row.key,
                summary=row.issue.summary,
                state_name=row.state_name,
                state_category=row.state_category,
                priority=row.issue.priority,
                created_at=row.issue.created_at,
                updated_at=row.issue.updated_at,
            )
            for row in await service.to_summaries(page.items)
        ],
        next_cursor=page.next_cursor,
        total=page.total,
    )


def issue_id_column() -> Any:
    """`issue.id` 컬럼. 다른 모듈이 자기 테이블과 이 이슈를 잇는 조건을
    만들 때 쓴다 — 모델 자체를 내주지 않으려고 컬럼 하나만 낸다."""
    return Issue.id


@dataclass(frozen=True, slots=True)
class StateRef:
    """워크플로우 상태 하나. 다른 모듈이 상태를 **고르게** 할 때 쓴다.

    `workflow_name` 을 함께 내는 이유: 같은 이름의 상태가 워크플로우마다
    따로 있다("대기" 가 버그 워크플로우에도, 문의 워크플로우에도 있다).
    이름만 보여 주면 목록에 똑같은 줄이 둘 뜨고 어느 쪽을 고른 건지 알 수
    없다.
    """

    id: UUID
    name: str
    category: str
    workflow_name: str


async def get_project_states(session: AsyncSession, project_id: UUID) -> list[StateRef]:
    """이 프로젝트의 이슈가 있을 수 있는 상태 전부.

    프로젝트에서 쓸 수 있는 이슈 유형 → 그 유형들의 워크플로우 → 상태.
    `desk` 의 SLA 정책이 "이 상태에서는 시계를 멈춘다" 를 고르는 데 쓴다.
    """
    from ieum.modules.issues.models import Workflow, WorkflowState
    from ieum.modules.issues.repository import IssueTypeRepository

    types = await IssueTypeRepository(session).available_for(project_id)
    workflow_ids = {t.workflow_id for t in types}
    if not workflow_ids:
        return []
    rows = (
        await session.execute(
            select(WorkflowState, Workflow.name)
            .join(Workflow, Workflow.id == WorkflowState.workflow_id)
            .where(WorkflowState.workflow_id.in_(workflow_ids))
            .order_by(Workflow.name, WorkflowState.position, WorkflowState.name)
        )
    ).all()
    return [
        StateRef(
            id=state.id,
            name=state.name,
            category=state.category,
            workflow_name=workflow_name,
        )
        for state, workflow_name in rows
    ]
