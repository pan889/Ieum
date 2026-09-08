"""이슈 서비스 테스트. 실제 Postgres 를 쓴다."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.outbox import OutboxEvent
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueType,
    SecurityLevel,
    Version,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.repository import HistoryRepository
from ieum.modules.issues.service import (
    CommentService,
    IssueService,
    NewIssue,
    SecurityLevelGuard,
)
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    from ieum.modules.issues.models import Issue

    service.register_guard(Issue, SecurityLevelGuard())
    return service


@pytest_asyncio.fixture
async def workflow(session: AsyncSession) -> AsyncIterator[Workflow]:
    """테스트마다 격리된 기본 워크플로우. 시드와 같은 정의를 쓴다."""
    wf = Workflow(name=f"WF-{secrets.token_hex(4)}", is_builtin=False)
    session.add(wf)
    await session.flush()

    states: dict[str, WorkflowState] = {}
    for position, (name, category, is_initial) in enumerate(DEFAULT_WORKFLOW_STATES):
        state = WorkflowState(
            workflow_id=wf.id,
            name=name,
            category=category,
            position=position,
            is_initial=is_initial,
        )
        session.add(state)
        states[name] = state
    await session.flush()

    for position, (name, from_name, to_name, post) in enumerate(DEFAULT_TRANSITIONS):
        session.add(
            WorkflowTransition(
                workflow_id=wf.id,
                name=name,
                from_state_id=states[from_name].id if from_name else None,
                to_state_id=states[to_name].id,
                conditions=[],
                post_functions=post,
                position=position,
            )
        )
    await session.flush()
    yield wf


@pytest_asyncio.fixture
async def issue_type(session: AsyncSession, workflow: Workflow) -> AsyncIterator[IssueType]:
    row = IssueType(project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=workflow.id)
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def project(session: AsyncSession) -> AsyncIterator[Project]:
    row = Project(key=f"P{secrets.token_hex(3).upper()}", name="Test Project")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"u-{new_id()}@example.com", display_name="Tester", status="active")
    session.add(row)
    await session.flush()
    yield row


def actor_for(user: User) -> Actor:
    return Actor(user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow())


async def grant(
    session: AsyncSession,
    *,
    principal_id: UUID,
    granted: tuple[str, ...],
    scope: Scope,
) -> None:
    repo = RoleRepository(session)
    role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind=scope.kind.value)
    repo.add(role)
    await session.flush()
    for permission in granted:
        repo.grant(role.id, permission)
    repo.assign(role_id=role.id, scope=scope, principal_kind="user", principal_id=principal_id)
    await session.flush()


async def full_access(session: AsyncSession, user: User, project: Project) -> Actor:
    await grant(
        session,
        principal_id=user.id,
        granted=perms.project_scoped(),
        scope=Scope.project(project.id),
    )
    return actor_for(user)


class TestCreate:
    async def test_requires_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await IssueService(session, permissions).create(
                actor_for(user), NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
            )

    async def test_key_sequence_increments_per_project(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        keys = [
            (
                await service.create(
                    actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary=f"#{i}")
                )
            ).key
            for i in range(3)
        ]
        assert keys == [f"{project.key}-1", f"{project.key}-2", f"{project.key}-3"]

    async def test_starts_in_initial_state(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        assert view.state_name == "Open"
        assert view.state_category == "todo"

    async def test_labels_deduped_and_sorted(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                labels=["b", "a", "b", " a "],
            ),
        )
        assert view.labels == ["a", "b"]

    @pytest.mark.parametrize("summary", ["", "   "])
    async def test_blank_summary_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
        summary: str,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary=summary)
            )
        assert exc.value.code == "issues.summary_required"

    async def test_archived_project_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        project.archived_at = utcnow()
        await session.flush()
        with pytest.raises(ConflictError) as exc:
            await IssueService(session, permissions).create(
                actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
            )
        assert exc.value.code == "issues.project_archived"

    async def test_assigning_needs_assign_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_CREATE, perms.ISSUE_VIEW),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await IssueService(session, permissions).create(
                actor_for(user),
                NewIssue(
                    project_id=project.id, type_id=issue_type.id, summary="x", assignee_id=user.id
                ),
            )

    async def test_publishes_created_event(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == view.issue.id)
                )
            )
            .scalars()
            .all()
        )
        assert [r.event_type for r in rows] == ["issue.created"]


class TestHierarchy:
    async def test_parent_must_be_same_project(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="p")
        )

        other = Project(key=f"O{secrets.token_hex(3).upper()}", name="Other")
        session.add(other)
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_CREATE, perms.ISSUE_VIEW),
            scope=Scope.project(other.id),
        )
        with pytest.raises(ValidationError) as exc:
            await service.create(
                actor,
                NewIssue(
                    project_id=other.id,
                    type_id=issue_type.id,
                    summary="c",
                    parent_id=parent.issue.id,
                ),
            )
        assert exc.value.code == "issues.parent_in_other_project"

    async def test_depth_limit(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent_id: UUID | None = None
        for depth in range(3):
            created = await service.create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary=f"L{depth}",
                    parent_id=parent_id,
                ),
            )
            parent_id = created.issue.id
        with pytest.raises(ValidationError) as exc:
            await service.create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary="deep",
                    parent_id=parent_id,
                ),
            )
        assert exc.value.code == "issues.subtask_too_deep"

    async def test_cycle_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """A→B 인데 A 의 부모를 B 로 만들면 롤업 계산이 무한 루프에 빠진다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        a = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="a")
        )
        b = await service.create(
            actor,
            NewIssue(
                project_id=project.id, type_id=issue_type.id, summary="b", parent_id=a.issue.id
            ),
        )
        with pytest.raises(ValidationError) as exc:
            await service.update(actor, a.issue.id, {"parent_id": b.issue.id})
        assert exc.value.code == "issues.parent_cycle"

    async def test_archive_blocked_with_children(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="p")
        )
        await service.create(
            actor,
            NewIssue(
                project_id=project.id, type_id=issue_type.id, summary="c", parent_id=parent.issue.id
            ),
        )
        with pytest.raises(ConflictError) as exc:
            await service.archive(actor, parent.issue.id)
        assert exc.value.code == "issues.has_active_children"


class TestUpdate:
    async def test_edit_own_permission_is_scoped_to_reporter(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """edit_own 만 있으면 남이 만든 이슈는 못 고친다."""
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_VIEW, perms.ISSUE_CREATE, perms.ISSUE_EDIT_OWN),
            scope=Scope.project(project.id),
        )
        actor = actor_for(user)
        service = IssueService(session, permissions)
        mine = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="mine")
        )
        await service.update(actor, mine.issue.id, {"summary": "고침"})

        stranger = User(email=f"s-{new_id()}@example.com", display_name="S", status="active")
        session.add(stranger)
        await session.flush()
        mine.issue.reporter_id = stranger.id
        await session.flush()

        with pytest.raises(PermissionDeniedError):
            await service.update(actor, mine.issue.id, {"summary": "다시"})

    async def test_optimistic_lock(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        stale = view.issue.version

        await service.update(actor, view.issue.id, {"summary": "먼저"})
        with pytest.raises(OptimisticLockError) as exc:
            await service.update(actor, view.issue.id, {"summary": "나중"}, expected_version=stale)
        assert exc.value.status_code == 409

    async def test_no_op_update_does_not_bump_version(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """같은 값으로 저장했을 때 버전이 올라가면 남의 편집을 헛되게 막는다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        again = await service.update(actor, view.issue.id, {"summary": "x"})
        assert again.issue.version == view.issue.version

    async def test_non_editable_field_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        with pytest.raises(ValidationError) as exc:
            await service.update(actor, view.issue.id, {"key_seq": 99})
        assert exc.value.code == "issues.field_not_editable"

    async def test_history_records_diff(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="처음")
        )
        await service.update(actor, view.issue.id, {"summary": "나중", "priority": 1})

        entries = await HistoryRepository(session).for_issue(view.issue.id)
        assert len(entries) == 1
        by_field = {c["field"]: c for c in entries[0].changes}
        assert by_field["summary"] == {"field": "summary", "from": "처음", "to": "나중"}
        assert by_field["priority"]["to"] == 1


class TestTransitions:
    async def test_available_transitions_from_initial(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        names = {t.name for t in await service.available_transitions(actor, view.issue.id)}
        # Open 에서 나가는 전이 + from_state 가 NULL 인 전역 전이(Reopen)
        assert names == {"Start progress", "Reopen"}

    async def test_post_functions_applied(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        assert view.issue.assignee_id is None

        start = next(
            t
            for t in await service.available_transitions(actor, view.issue.id)
            if t.name == "Start progress"
        )
        after = await service.transition(actor, view.issue.id, start.id)
        # assign_to_actor 후처리
        assert after.issue.assignee_id == actor.user_id
        assert after.state_name == "In Progress"

    async def test_resolve_sets_resolved_at_and_progress(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        start = next(
            t
            for t in await service.available_transitions(actor, view.issue.id)
            if t.name == "Start progress"
        )
        await service.transition(actor, view.issue.id, start.id)
        resolve = next(
            t
            for t in await service.available_transitions(actor, view.issue.id)
            if t.name == "Resolve"
        )
        after = await service.transition(actor, view.issue.id, resolve.id)

        assert after.state_category == "done"
        assert after.issue.progress == 100
        assert after.issue.resolved_at is not None

    async def test_reopen_clears_resolution(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        for name in ("Start progress", "Resolve", "Reopen"):
            found = next(
                t
                for t in await service.available_transitions(actor, view.issue.id)
                if t.name == name
            )
            after = await service.transition(actor, view.issue.id, found.id)
        assert after.state_name == "Open"
        assert after.issue.resolved_at is None
        assert after.issue.progress == 0

    async def test_transition_not_available_from_current_state(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        resolve_row = (
            await session.execute(
                select(WorkflowTransition)
                .where(WorkflowTransition.workflow_id == issue_type.workflow_id)
                .where(WorkflowTransition.name == "Resolve")
            )
        ).scalar_one()

        with pytest.raises(ConflictError) as exc:
            await service.transition(actor, view.issue.id, resolve_row.id)
        assert exc.value.code == "issues.transition_not_available"

    async def test_blocked_condition_raises_with_reasons(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        row = (
            await session.execute(
                select(WorkflowTransition)
                .where(WorkflowTransition.workflow_id == issue_type.workflow_id)
                .where(WorkflowTransition.name == "Start progress")
            )
        ).scalar_one()
        row.conditions = [{"type": "assignee_set"}]
        await session.flush()

        with pytest.raises(PermissionDeniedError) as exc:
            await service.transition(actor, view.issue.id, row.id)
        assert exc.value.details["conditions"] == ["assignee_set"]

    async def test_transition_from_other_workflow_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
        workflow: Workflow,
    ) -> None:
        """다른 워크플로우의 전이 ID 로 상태를 갈아치울 수 없어야 한다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        other = Workflow(name=f"WF2-{secrets.token_hex(4)}")
        session.add(other)
        await session.flush()
        state = WorkflowState(workflow_id=other.id, name="Weird", category="done", is_initial=True)
        session.add(state)
        await session.flush()
        rogue = WorkflowTransition(
            workflow_id=other.id, name="Rogue", from_state_id=None, to_state_id=state.id
        )
        session.add(rogue)
        await session.flush()

        with pytest.raises(NotFoundError):
            await service.transition(actor, view.issue.id, rogue.id)


class TestCustomFields:
    async def test_unknown_field_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary="x",
                    custom_fields={"nope": 1},
                ),
            )
        assert exc.value.code == "issues.unknown_custom_field"

    async def test_value_validated_and_field_named_in_error(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """어느 필드에서 났는지 알려주지 않으면 폼에서 표시할 수 없다."""
        session.add(
            FieldDefinition(
                key="severity",
                name="Severity",
                kind="select",
                config={"options": ["low", "high"]},
            )
        )
        await session.flush()
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary="x",
                    custom_fields={"severity": "nope"},
                ),
            )
        assert exc.value.details["field"] == "severity"

    async def test_required_field_enforced_on_create(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        session.add(FieldDefinition(key="team", name="Team", kind="text", is_required=True))
        await session.flush()
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
            )
        assert exc.value.code == "issues.required_custom_field_missing"
        assert exc.value.details["fields"] == ["team"]

    async def test_values_roundtrip(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        session.add(
            FieldDefinition(
                key="tags", name="Tags", kind="multi_select", config={"options": ["a", "b"]}
            )
        )
        await session.flush()
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                custom_fields={"tags": ["b", "a"]},
            ),
        )
        assert view.custom_fields["tags"] == ["b", "a"]

    async def test_unknown_user_reference_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """UUID 모양만 맞으면 통과하던 구멍. 화면이 이름을 못 찾게 된다."""
        session.add(FieldDefinition(key="owner", name="Owner", kind="user"))
        await session.flush()
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary="x",
                    custom_fields={"owner": str(new_id())},
                ),
            )
        assert exc.value.code == "issues.invalid_field_reference"
        assert exc.value.details["fields"] == ["owner"]

    async def test_existing_user_reference_accepted(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        session.add(FieldDefinition(key="owner", name="Owner", kind="user"))
        await session.flush()
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                custom_fields={"owner": str(user.id)},
            ),
        )
        assert view.custom_fields["owner"] == str(user.id)

    async def test_version_from_another_project_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """버전은 프로젝트에 매인다. 남의 프로젝트 버전을 밀어 넣지 못한다."""
        other = Project(key=f"OTH{secrets.token_hex(2).upper()}", name="Other")
        session.add(other)
        await session.flush()
        elsewhere = Version(project_id=other.id, name="1.0")
        session.add_all([elsewhere, FieldDefinition(key="fix", name="Fix", kind="version")])
        await session.flush()

        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await IssueService(session, permissions).create(
                actor,
                NewIssue(
                    project_id=project.id,
                    type_id=issue_type.id,
                    summary="x",
                    custom_fields={"fix": str(elsewhere.id)},
                ),
            )
        assert exc.value.code == "issues.invalid_field_reference"

    async def test_version_in_project_accepted_and_listed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        mine = Version(project_id=project.id, name="1.0")
        session.add_all([mine, FieldDefinition(key="fix", name="Fix", kind="version")])
        await session.flush()

        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                custom_fields={"fix": str(mine.id)},
            ),
        )
        assert view.custom_fields["fix"] == str(mine.id)
        assert [v.id for v in await service.list_versions(actor, project.id)] == [mine.id]

    async def test_released_versions_still_listed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        """이미 그 버전을 가리키는 이슈가 있다. 빼면 편집 화면이 UUID 를 띄운다."""
        session.add_all(
            [
                Version(project_id=project.id, name="1.0", status="released"),
                Version(project_id=project.id, name="2.0", status="open"),
            ]
        )
        await session.flush()
        actor = await full_access(session, user, project)
        rows = await IssueService(session, permissions).list_versions(actor, project.id)
        # 미출시가 먼저 온다.
        assert [v.name for v in rows] == ["2.0", "1.0"]

    async def test_clearing_a_value_on_update(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """null 은 '비움'이다. 상세 화면의 필드 지우기가 이 경로를 쓴다."""
        session.add(FieldDefinition(key="note", name="Note", kind="text"))
        await session.flush()
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                custom_fields={"note": "keep"},
            ),
        )
        after = await service.update(actor, view.issue.id, {}, custom_fields={"note": None})
        assert "note" not in after.custom_fields


class TestSecurityLevel:
    async def test_hidden_from_non_grantee(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """스코프 권한이 있어도 보안 레벨에서 막힌다 (auth.md 5절 객체 수준 제한)."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="secret")
        )

        insider = User(email=f"i-{new_id()}@e.com", display_name="I", status="active")
        session.add(insider)
        await session.flush()
        level = SecurityLevel(
            project_id=project.id,
            name="Restricted",
            grantees=[{"kind": "user", "id": str(insider.id)}],
        )
        session.add(level)
        await session.flush()
        view.issue.security_level_id = level.id
        await session.flush()

        with pytest.raises(PermissionDeniedError):
            await IssueService(session, permissions).get(actor_for(user), view.issue.id)

    async def test_visible_to_grantee(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="secret")
        )

        level = SecurityLevel(
            project_id=project.id,
            name="Restricted",
            grantees=[{"kind": "user", "id": str(user.id)}],
        )
        session.add(level)
        await session.flush()
        view.issue.security_level_id = level.id
        await session.flush()

        assert await IssueService(session, permissions).get(actor_for(user), view.issue.id)

    async def test_deleting_level_restores_visibility(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """레벨을 지우면 제한도 풀려야 한다. FK 가 ON DELETE SET NULL 이라
        참조가 끊기고, 아무도 못 보는 이슈가 남지 않는다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        view = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        insider = User(email=f"i-{new_id()}@e.com", display_name="I", status="active")
        session.add(insider)
        await session.flush()
        level = SecurityLevel(
            project_id=project.id,
            name="Restricted",
            grantees=[{"kind": "user", "id": str(insider.id)}],
        )
        session.add(level)
        await session.flush()
        view.issue.security_level_id = level.id
        await session.flush()

        with pytest.raises(PermissionDeniedError):
            await IssueService(session, permissions).get(actor_for(user), view.issue.id)

        await session.delete(level)
        await session.flush()
        await session.refresh(view.issue)
        assert view.issue.security_level_id is None
        assert await IssueService(session, permissions).get(actor_for(user), view.issue.id)


class TestComments:
    async def test_internal_notes_hidden_without_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """내부 노트는 SQL 단계에서 걸러야 한다. 가져와서 거르면 사고가 난다."""
        author = await full_access(session, user, project)
        issues = IssueService(session, permissions)
        view = await issues.create(
            author, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        comments = CommentService(session, permissions)
        await comments.add(author, view.issue.id, "공개 코멘트")
        await comments.add(author, view.issue.id, "내부 노트", is_internal=True)

        outsider = User(email=f"o-{new_id()}@e.com", display_name="O", status="active")
        session.add(outsider)
        await session.flush()
        await grant(
            session,
            principal_id=outsider.id,
            granted=(perms.ISSUE_VIEW,),
            scope=Scope.project(project.id),
        )

        visible = await comments.list_for(actor_for(outsider), view.issue.id)
        assert [c.body for c in visible] == ["공개 코멘트"]

        # 권한이 있는 쪽은 둘 다 본다.
        assert len(await comments.list_for(author, view.issue.id)) == 2

    async def test_internal_note_requires_permission_to_write(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        author = await full_access(session, user, project)
        issues = IssueService(session, permissions)
        view = await issues.create(
            author, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )

        outsider = User(email=f"o-{new_id()}@e.com", display_name="O", status="active")
        session.add(outsider)
        await session.flush()
        await grant(
            session,
            principal_id=outsider.id,
            granted=(perms.ISSUE_VIEW, perms.COMMENT_ADD),
            scope=Scope.project(project.id),
        )
        comments = CommentService(session, permissions)
        with pytest.raises(PermissionDeniedError):
            await comments.add(actor_for(outsider), view.issue.id, "몰래", is_internal=True)

    async def test_cannot_edit_others_comment(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        author = await full_access(session, user, project)
        issues = IssueService(session, permissions)
        view = await issues.create(
            author, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        comments = CommentService(session, permissions)
        comment = await comments.add(author, view.issue.id, "내 것")

        outsider = User(email=f"o-{new_id()}@e.com", display_name="O", status="active")
        session.add(outsider)
        await session.flush()
        await grant(
            session,
            principal_id=outsider.id,
            granted=(perms.ISSUE_VIEW, perms.COMMENT_ADD, perms.COMMENT_EDIT_OWN),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await comments.edit(actor_for(outsider), comment.id, "가로채기")

    async def test_empty_body_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        author = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            author, NewIssue(project_id=project.id, type_id=issue_type.id, summary="x")
        )
        with pytest.raises(ValidationError) as exc:
            await CommentService(session, permissions).add(author, view.issue.id, "   ")
        assert exc.value.code == "issues.comment_empty"


class TestListing:
    async def test_only_visible_projects(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="보임")
        )

        hidden = Project(key=f"H{secrets.token_hex(3).upper()}", name="Hidden")
        session.add(hidden)
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_CREATE, perms.ISSUE_VIEW),
            scope=Scope.project(hidden.id),
        )
        creator = actor_for(user)
        await service.create(
            creator, NewIssue(project_id=hidden.id, type_id=issue_type.id, summary="숨김")
        )

        # 첫 액터의 ACL 은 project 스코프 하나뿐이다.
        page = await service.list_for(actor, PageRequest(limit=50), project_id=project.id)
        assert [i.summary for i in page.items] == ["보임"]

    async def test_archived_excluded_by_default(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        live = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="살아있음")
        )
        gone = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="아카이브")
        )
        await service.archive(actor, gone.issue.id)
        await session.flush()

        default = await service.list_for(
            actor_for(user), PageRequest(limit=50), project_id=project.id
        )
        assert {i.summary for i in default.items} == {"살아있음"}

        everything = await service.list_for(
            actor_for(user), PageRequest(limit=50), project_id=project.id, include_archived=True
        )
        assert {i.summary for i in everything.items} == {"살아있음", "아카이브"}
        assert live.issue.id in {i.id for i in everything.items}


class TestMentions:
    """멘션은 issues 가 권한을 확인해서 이벤트에 싣는다.

    notify 는 이슈 ACL 을 못 본다. 여기서 안 거르면 아무나 멘션해서 비공개
    이슈의 제목을 알림으로 흘릴 수 있다.
    """

    async def _outsider(self, session: AsyncSession) -> User:
        row = User(email=f"out-{new_id()}@example.com", display_name="Outsider", status="active")
        session.add(row)
        await session.flush()
        return row

    def _mention(self, user: User) -> str:
        return f"cc [@{user.display_name}](user:{user.id})"

    async def test_mention_of_a_permitted_user_reaches_the_event(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        teammate = await self._outsider(session)
        await grant(
            session,
            principal_id=teammate.id,
            granted=(perms.ISSUE_VIEW,),
            scope=Scope.project(project.id),
        )
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(project_id=project.id, type_id=issue_type.id, summary="x"),
        )
        await CommentService(session, permissions).add(
            actor, view.issue.id, self._mention(teammate)
        )
        await session.flush()

        event = await _latest_outbox(session, "issue.commented")
        assert event.payload["mentioned_ids"] == [str(teammate.id)]

    async def test_mention_of_an_outsider_is_dropped(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        outsider = await self._outsider(session)
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(project_id=project.id, type_id=issue_type.id, summary="secret summary"),
        )
        await CommentService(session, permissions).add(
            actor, view.issue.id, self._mention(outsider)
        )
        await session.flush()

        event = await _latest_outbox(session, "issue.commented")
        assert event.payload["mentioned_ids"] == []

    async def test_internal_note_mention_needs_internal_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """내부 노트 알림이 내부 노트를 못 보는 사람에게 가면 안 된다."""
        actor = await full_access(session, user, project)
        teammate = await self._outsider(session)
        await grant(
            session,
            principal_id=teammate.id,
            granted=(perms.ISSUE_VIEW,),  # 내부 노트 권한은 없다
            scope=Scope.project(project.id),
        )
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(project_id=project.id, type_id=issue_type.id, summary="x"),
        )
        await CommentService(session, permissions).add(
            actor, view.issue.id, self._mention(teammate), is_internal=True
        )
        await session.flush()

        event = await _latest_outbox(session, "issue.commented")
        assert event.payload["mentioned_ids"] == []

    async def test_description_mention_only_fires_when_the_body_changes(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """우선순위만 고쳐도 멘션 알림이 다시 나가면 사람들이 알림을 끈다."""
        actor = await full_access(session, user, project)
        teammate = await self._outsider(session)
        await grant(
            session,
            principal_id=teammate.id,
            granted=(perms.ISSUE_VIEW,),
            scope=Scope.project(project.id),
        )
        service = IssueService(session, permissions)
        view = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="x",
                description=self._mention(teammate),
            ),
        )
        created = await _latest_outbox(session, "issue.created")
        assert created.payload["mentioned_ids"] == [str(teammate.id)]

        await service.update(actor, view.issue.id, {"priority": 1})
        await session.flush()
        updated = await _latest_outbox(session, "issue.updated")
        assert updated.payload["mentioned_ids"] == []


async def _latest_outbox(session: AsyncSession, event_type: str) -> OutboxEvent:
    row = (
        await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .limit(1)
        )
    ).scalar_one()
    return row


class TestProgressRollup:
    """부모 진행률은 자식에서 계산한다. 직접 적어 두면 두 값이 어긋난다."""

    async def _tree(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
        *,
        children: int = 2,
    ) -> tuple[UUID, list[UUID]]:
        service = IssueService(session, permissions)
        parent = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="parent")
        )
        kids = [
            (
                await service.create(
                    actor,
                    NewIssue(
                        project_id=project.id,
                        type_id=issue_type.id,
                        summary=f"child {i}",
                        parent_id=parent.issue.id,
                    ),
                )
            ).issue.id
            for i in range(children)
        ]
        return parent.issue.id, kids

    async def test_child_progress_rolls_up(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent_id, kids = await self._tree(session, permissions, actor, project, issue_type)

        await service.update(actor, kids[0], {"progress": 100})
        await session.flush()
        parent = await session.get(Issue, parent_id)
        assert parent is not None
        assert parent.progress == 50

    async def test_parent_progress_cannot_be_set_by_hand(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """조용히 되돌리는 대신 지금 거절한다."""
        actor = await full_access(session, user, project)
        parent_id, _ = await self._tree(session, permissions, actor, project, issue_type)
        with pytest.raises(ValidationError, match="직접 바꿀 수 없다"):
            await IssueService(session, permissions).update(actor, parent_id, {"progress": 90})

    async def test_archiving_a_child_removes_it_from_the_average(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """취소한 일 때문에 부모가 영원히 100% 가 안 되면 숫자를 안 믿는다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent_id, kids = await self._tree(session, permissions, actor, project, issue_type)

        await service.update(actor, kids[0], {"progress": 100})
        await service.archive(actor, kids[1])
        await session.flush()
        parent = await session.get(Issue, parent_id)
        assert parent is not None
        assert parent.progress == 100

    async def test_rollup_reaches_grandparents(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        top = await service.create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="top")
        )
        mid = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="mid",
                parent_id=top.issue.id,
            ),
        )
        leaf = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="leaf",
                parent_id=mid.issue.id,
            ),
        )
        await service.update(actor, leaf.issue.id, {"progress": 100})
        await session.flush()

        for issue_id in (mid.issue.id, top.issue.id):
            row = await session.get(Issue, issue_id)
            assert row is not None
            assert row.progress == 100

    async def test_transition_post_function_rolls_up(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """Resolve 는 후처리로 progress 를 100 으로 만든다. 부모도 따라와야 한다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        parent_id, kids = await self._tree(
            session, permissions, actor, project, issue_type, children=1
        )
        start = next(
            t
            for t in await service.available_transitions(actor, kids[0])
            if t.name == "Start progress"
        )
        await service.transition(actor, kids[0], start.id)
        resolve = next(
            t for t in await service.available_transitions(actor, kids[0]) if t.name == "Resolve"
        )
        await service.transition(actor, kids[0], resolve.id)
        await session.flush()

        parent = await session.get(Issue, parent_id)
        assert parent is not None
        assert parent.progress == 100

    async def test_leaf_progress_is_still_editable(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        view = await IssueService(session, permissions).create(
            actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="leaf")
        )
        updated = await IssueService(session, permissions).update(
            actor, view.issue.id, {"progress": 42}
        )
        assert updated.issue.progress == 42
