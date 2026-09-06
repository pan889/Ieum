"""issues 데이터 접근. 쿼리만 한다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import Acl
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueComment,
    IssueFieldValue,
    IssueHistory,
    IssueLabel,
    IssueLink,
    IssueType,
    SecurityLevel,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)


class WorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, workflow_id: UUID) -> Workflow | None:
        return await self._s.get(Workflow, workflow_id)

    async def get_by_name(self, name: str) -> Workflow | None:
        stmt = select(Workflow).where(Workflow.name == name)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, workflow: Workflow) -> Workflow:
        self._s.add(workflow)
        return workflow

    async def state(self, state_id: UUID) -> WorkflowState | None:
        return await self._s.get(WorkflowState, state_id)

    async def states_by_ids(self, state_ids: Sequence[UUID]) -> dict[UUID, WorkflowState]:
        """여러 상태를 한 번에. 목록 화면이 행마다 조회하지 않게 한다."""
        unique = list(dict.fromkeys(state_ids))
        if not unique:
            return {}
        stmt = select(WorkflowState).where(WorkflowState.id.in_(unique))
        return {row.id: row for row in (await self._s.execute(stmt)).scalars()}

    async def states_of(self, workflow_id: UUID) -> list[WorkflowState]:
        stmt = (
            select(WorkflowState)
            .where(WorkflowState.workflow_id == workflow_id)
            .order_by(WorkflowState.position, WorkflowState.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def initial_state(self, workflow_id: UUID) -> WorkflowState | None:
        stmt = (
            select(WorkflowState)
            .where(WorkflowState.workflow_id == workflow_id)
            .where(WorkflowState.is_initial.is_(True))
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def transitions_from(
        self, workflow_id: UUID, from_state_id: UUID
    ) -> list[WorkflowTransition]:
        """해당 상태에서 나갈 수 있는 전이. from_state_id 가 NULL 인 전역 전이 포함."""
        stmt = (
            select(WorkflowTransition)
            .where(WorkflowTransition.workflow_id == workflow_id)
            .where(
                (WorkflowTransition.from_state_id == from_state_id)
                | WorkflowTransition.from_state_id.is_(None)
            )
            .order_by(WorkflowTransition.position, WorkflowTransition.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def transition(self, transition_id: UUID) -> WorkflowTransition | None:
        return await self._s.get(WorkflowTransition, transition_id)


class IssueTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, type_id: UUID) -> IssueType | None:
        return await self._s.get(IssueType, type_id)

    def add(self, issue_type: IssueType) -> IssueType:
        self._s.add(issue_type)
        return issue_type

    async def available_for(self, project_id: UUID) -> list[IssueType]:
        """전역 유형 + 이 프로젝트 전용 유형."""
        stmt = (
            select(IssueType)
            .where(IssueType.project_id.is_(None) | (IssueType.project_id == project_id))
            .order_by(IssueType.position, IssueType.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def default_for(self, project_id: UUID) -> IssueType | None:
        types = await self.available_for(project_id)
        return next((t for t in types if not t.is_subtask), None)


class FieldDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_key(self, key: str) -> FieldDefinition | None:
        stmt = select(FieldDefinition).where(FieldDefinition.key == key)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, definition: FieldDefinition) -> FieldDefinition:
        self._s.add(definition)
        return definition

    async def applicable_to(
        self, *, project_id: UUID, issue_type_id: UUID
    ) -> list[FieldDefinition]:
        """이 프로젝트·유형에 보이는 필드 정의.

        NULL 은 '제한 없음'이다. 전역 필드는 어디서나 보이고, 프로젝트가
        지정된 필드는 그 프로젝트에서만 보인다.
        """
        stmt = (
            select(FieldDefinition)
            .where(
                FieldDefinition.project_id.is_(None) | (FieldDefinition.project_id == project_id)
            )
            .where(
                FieldDefinition.issue_type_id.is_(None)
                | (FieldDefinition.issue_type_id == issue_type_id)
            )
            .order_by(FieldDefinition.position, FieldDefinition.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())


class IssueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, issue_id: UUID) -> Issue | None:
        return await self._s.get(Issue, issue_id)

    async def get_by_key_seq(self, project_id: UUID, key_seq: int) -> Issue | None:
        stmt = select(Issue).where(Issue.project_id == project_id).where(Issue.key_seq == key_seq)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, issue: Issue) -> Issue:
        self._s.add(issue)
        return issue

    async def get_many(self, issue_ids: Sequence[UUID]) -> list[Issue]:
        if not issue_ids:
            return []
        stmt = select(Issue).where(Issue.id.in_(issue_ids))
        return list((await self._s.execute(stmt)).scalars().all())

    async def children_of(self, parent_id: UUID) -> list[Issue]:
        stmt = select(Issue).where(Issue.parent_id == parent_id).where(Issue.archived_at.is_(None))
        return list((await self._s.execute(stmt)).scalars().all())

    async def ancestor_ids(self, issue_id: UUID) -> list[UUID]:
        """자신을 포함한 조상. 부모-자식 순환을 막는 데 쓴다."""
        base = (
            select(Issue.id, Issue.parent_id)
            .where(Issue.id == issue_id)
            .cte("issue_ancestors", recursive=True)
        )
        parent = Issue.__table__.alias("p")
        base = base.union_all(
            select(parent.c.id, parent.c.parent_id).join(base, base.c.parent_id == parent.c.id)
        )
        return list((await self._s.execute(select(base.c.id))).scalars().all())

    async def list_page(
        self,
        request: PageRequest,
        *,
        acl: Acl,
        project_id: UUID | None = None,
        include_archived: bool = False,
    ) -> Page[Issue]:
        """목록은 권한 검사가 아니라 필터링이다 (auth.md 5절)."""
        if acl.is_empty:
            return Page(items=[])

        stmt: Select[tuple[Issue]] = select(Issue)
        if not acl.is_global:
            stmt = stmt.where(Issue.project_id.in_(acl.project_ids))
        if project_id is not None:
            stmt = stmt.where(Issue.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(Issue.archived_at.is_(None))

        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(Issue.id > UUID(payload["id"]))
        # UUIDv7 은 시간 정렬이 되므로 id 하나로 안정적인 커서가 된다.
        stmt = stmt.order_by(Issue.id).limit(request.fetch_limit)

        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(rows, request, lambda i: {"id": str(i.id)})

    async def count_in_project(self, project_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Issue)
            .where(Issue.project_id == project_id)
            .where(Issue.archived_at.is_(None))
        )
        return int((await self._s.execute(stmt)).scalar_one())


class IssueFieldValueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_issue(self, issue_id: UUID) -> dict[str, Any]:
        stmt = select(IssueFieldValue).where(IssueFieldValue.issue_id == issue_id)
        rows = (await self._s.execute(stmt)).scalars().all()
        return {row.field_key: row.value for row in rows}

    async def set(self, issue_id: UUID, field_key: str, value: Any) -> None:
        existing = await self._s.get(IssueFieldValue, (issue_id, field_key))
        if value is None:
            if existing is not None:
                await self._s.delete(existing)
            return
        if existing is None:
            self._s.add(IssueFieldValue(issue_id=issue_id, field_key=field_key, value=value))
        else:
            existing.value = value


class IssueLabelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_issue(self, issue_id: UUID) -> list[str]:
        stmt = (
            select(IssueLabel.label)
            .where(IssueLabel.issue_id == issue_id)
            .order_by(IssueLabel.label)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def replace(self, issue_id: UUID, labels: list[str]) -> None:
        await self._s.execute(delete(IssueLabel).where(IssueLabel.issue_id == issue_id))
        for label in sorted(set(labels)):
            self._s.add(IssueLabel(issue_id=issue_id, label=label))


class IssueLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, link: IssueLink) -> IssueLink:
        self._s.add(link)
        return link

    async def exists(self, from_id: UUID, to_id: UUID, kind: str) -> bool:
        stmt = (
            select(func.count())
            .select_from(IssueLink)
            .where(IssueLink.from_issue_id == from_id)
            .where(IssueLink.to_issue_id == to_id)
            .where(IssueLink.kind == kind)
        )
        return bool((await self._s.execute(stmt)).scalar_one())

    async def get(self, link_id: UUID) -> IssueLink | None:
        return await self._s.get(IssueLink, link_id)

    async def for_issue(self, issue_id: UUID) -> list[IssueLink]:
        stmt = select(IssueLink).where(
            (IssueLink.from_issue_id == issue_id) | (IssueLink.to_issue_id == issue_id)
        )
        return list((await self._s.execute(stmt)).scalars().all())


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, comment_id: UUID) -> IssueComment | None:
        return await self._s.get(IssueComment, comment_id)

    def add(self, comment: IssueComment) -> IssueComment:
        self._s.add(comment)
        return comment

    async def list_for_issue(self, issue_id: UUID, *, include_internal: bool) -> list[IssueComment]:
        """내부 노트는 권한이 있을 때만 SQL 단계에서 포함한다.

        가져온 뒤 걸러내면 실수 한 번에 고객에게 노출된다.
        """
        stmt = (
            select(IssueComment)
            .where(IssueComment.issue_id == issue_id)
            .where(IssueComment.archived_at.is_(None))
        )
        if not include_internal:
            stmt = stmt.where(IssueComment.is_internal.is_(False))
        stmt = stmt.order_by(IssueComment.created_at)
        return list((await self._s.execute(stmt)).scalars().all())


class HistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def record(
        self, *, issue_id: UUID, actor_id: UUID | None, changes: list[dict[str, Any]]
    ) -> IssueHistory | None:
        """변경이 없으면 기록하지 않는다. 빈 이력이 쌓이면 타임라인이 못 쓰게 된다."""
        if not changes:
            return None
        entry = IssueHistory(issue_id=issue_id, actor_id=actor_id, changes=changes)
        self._s.add(entry)
        return entry

    async def for_issue(self, issue_id: UUID) -> list[IssueHistory]:
        stmt = (
            select(IssueHistory)
            .where(IssueHistory.issue_id == issue_id)
            .order_by(IssueHistory.created_at)
        )
        return list((await self._s.execute(stmt)).scalars().all())


class SecurityLevelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, level_id: UUID) -> SecurityLevel | None:
        return await self._s.get(SecurityLevel, level_id)

    def add(self, level: SecurityLevel) -> SecurityLevel:
        self._s.add(level)
        return level


__all__ = [
    "CommentRepository",
    "FieldDefinitionRepository",
    "HistoryRepository",
    "IssueFieldValueRepository",
    "IssueLabelRepository",
    "IssueLinkRepository",
    "IssueRepository",
    "IssueTypeRepository",
    "SecurityLevelRepository",
    "WorkflowRepository",
]
