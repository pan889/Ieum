"""issues 의 공개 인터페이스.

`desk` 는 이슈 위에 얹히고(티켓 = 이슈), `wiki` 는 entity_link 로 참조한다.
둘 다 이 파일만 import 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.issues.models import Issue
from ieum.modules.issues.repository import IssueRepository, WorkflowRepository


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
