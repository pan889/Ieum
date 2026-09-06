"""시간 추적. 실제 Postgres 를 쓴다."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState, WorkflowTransition
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.issues.worklog import MAX_SPENT_MINUTES, WorklogService
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
    row = Project(key=f"W{secrets.token_hex(3).upper()}", name="Worklog Project")
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
    session: AsyncSession, *, principal_id: UUID, granted: tuple[str, ...], scope: Scope
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
        granted=perms.ALL[:-2],
        scope=Scope.project(project.id),
    )
    return actor_for(user)


async def make_issue(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    issue_type: IssueType,
    *,
    estimate: int | None = None,
) -> UUID:
    view = await IssueService(session, permissions).create(
        actor, NewIssue(project_id=project.id, type_id=issue_type.id, summary="timed")
    )
    if estimate is not None:
        view.issue.estimate_minutes = estimate
        await session.flush()
    return view.issue.id


class TestAdd:
    async def test_requires_worklog_add(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        owner = User(email=f"o-{new_id()}@example.com", display_name="Owner", status="active")
        session.add(owner)
        await session.flush()
        issue_id = await make_issue(
            session, permissions, await full_access(session, owner, project), project, issue_type
        )
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_VIEW,),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await WorklogService(session, permissions).add(
                actor_for(user), issue_id, spent_minutes=30
            )

    async def test_defaults_to_today(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        row = await WorklogService(session, permissions).add(actor, issue_id, spent_minutes=90)
        assert row.work_date == utcnow().date()
        assert row.user_id == user.id

    async def test_rejects_future_dates(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """아직 하지 않은 일을 기록할 수는 없다."""
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        with pytest.raises(ValidationError, match="미래"):
            await WorklogService(session, permissions).add(
                actor,
                issue_id,
                spent_minutes=30,
                work_date=(utcnow() + timedelta(days=1)).date(),
            )

    @pytest.mark.parametrize("minutes", [0, -5, MAX_SPENT_MINUTES + 1])
    async def test_rejects_out_of_range(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
        minutes: int,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        with pytest.raises(ValidationError):
            await WorklogService(session, permissions).add(actor, issue_id, spent_minutes=minutes)

    async def test_rejects_bool_as_minutes(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """bool 은 int 의 서브클래스다. True 가 1분이 되면 안 된다."""
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        with pytest.raises(ValidationError, match="정수"):
            await WorklogService(session, permissions).add(
                actor,
                issue_id,
                spent_minutes=True,  # type: ignore[arg-type]
            )

    async def test_normalizes_the_comment(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        row = await WorklogService(session, permissions).add(
            actor, issue_id, spent_minutes=30, comment="* a\n*  b\n"
        )
        assert row.comment == "- a\n- b"


class TestSummary:
    async def test_sums_from_rows_not_a_column(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """합계 컬럼을 두지 않는 이유가 여기 있다 — 삭제해도 즉시 맞는다."""
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type, estimate=300)
        service = WorklogService(session, permissions)
        first = await service.add(actor, issue_id, spent_minutes=60)
        await service.add(actor, issue_id, spent_minutes=90)

        summary = await service.summary(actor, issue_id)
        assert summary.spent_minutes == 150
        assert summary.remaining_minutes == 150
        assert summary.over_estimate is False

        await service.delete(actor, first.id)
        await session.flush()
        assert (await service.summary(actor, issue_id)).spent_minutes == 90

    async def test_flags_over_estimate(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type, estimate=60)
        await WorklogService(session, permissions).add(actor, issue_id, spent_minutes=120)
        summary = await WorklogService(session, permissions).summary(actor, issue_id)
        assert summary.remaining_minutes == -60
        assert summary.over_estimate is True

    async def test_no_estimate_means_no_remaining(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        await WorklogService(session, permissions).add(actor, issue_id, spent_minutes=30)
        summary = await WorklogService(session, permissions).summary(actor, issue_id)
        assert summary.remaining_minutes is None
        assert summary.over_estimate is False


class TestEditing:
    async def test_owner_can_edit_without_extra_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """오타를 고치는 데 별도 권한을 요구하면 아무도 시간을 안 적는다."""
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_VIEW, perms.ISSUE_CREATE, perms.WORKLOG_ADD),
            scope=Scope.project(project.id),
        )
        actor = actor_for(user)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        service = WorklogService(session, permissions)
        row = await service.add(actor, issue_id, spent_minutes=30)
        updated = await service.update(actor, row.id, spent_minutes=45)
        assert updated.spent_minutes == 45

    async def test_others_need_edit_any(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        owner = User(email=f"o-{new_id()}@example.com", display_name="Owner", status="active")
        session.add(owner)
        await session.flush()
        owner_actor = await full_access(session, owner, project)
        issue_id = await make_issue(session, permissions, owner_actor, project, issue_type)
        service = WorklogService(session, permissions)
        row = await service.add(owner_actor, issue_id, spent_minutes=30)

        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_VIEW, perms.WORKLOG_ADD),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await service.update(actor_for(user), row.id, spent_minutes=45)

    async def test_clear_comment_flag(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        service = WorklogService(session, permissions)
        row = await service.add(actor, issue_id, spent_minutes=30, comment="note")
        assert (await service.update(actor, row.id, clear_comment=True)).comment is None

    async def test_delete_removes_it(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type)
        service = WorklogService(session, permissions)
        row = await service.add(actor, issue_id, spent_minutes=30)
        await service.delete(actor, row.id)
        await session.flush()
        with pytest.raises(NotFoundError):
            await service.update(actor, row.id, spent_minutes=1)


class TestIqlTimespent:
    async def test_filters_by_recorded_time(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        busy = await make_issue(session, permissions, actor, project, issue_type)
        await make_issue(session, permissions, actor, project, issue_type)
        await WorklogService(session, permissions).add(actor, busy, spent_minutes=120)
        await session.flush()

        page = await SearchService(session, permissions).search(
            actor, f'project = "{project.key}" AND timespent > 60', PageRequest(limit=10)
        )
        assert [i.id for i in page.items] == [busy]

    async def test_no_worklog_counts_as_zero(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """NULL 이면 `timespent = 0` 이 아무것도 못 찾는다."""
        actor = await full_access(session, user, project)
        idle = await make_issue(session, permissions, actor, project, issue_type)
        page = await SearchService(session, permissions).search(
            actor, f'project = "{project.key}" AND timespent = 0', PageRequest(limit=10)
        )
        assert idle in [i.id for i in page.items]
