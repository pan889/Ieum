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

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
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
