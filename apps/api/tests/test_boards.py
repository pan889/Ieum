"""칸반 보드. 실제 Postgres 를 쓴다."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.boards import (
    DEFAULT_COLUMNS,
    MAX_COLUMNS,
    BoardService,
    ColumnResult,
)
from ieum.modules.issues.models import (
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
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
    row = Project(key=f"B{secrets.token_hex(3).upper()}", name="Board Project")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def other_project(session: AsyncSession) -> AsyncIterator[Project]:
    row = Project(key=f"O{secrets.token_hex(3).upper()}", name="Other Project")
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


async def full_access(session: AsyncSession, user: User, *projects: Project) -> Actor:
    for project in projects:
        await grant(
            session,
            principal_id=user.id,
            # 워크플로우·필드 정의 관리는 전역 권한이라 프로젝트 스코프에 못 준다.
            granted=perms.project_scoped(),
            scope=Scope.project(project.id),
        )
    return actor_for(user)


async def make_issue(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    issue_type: IssueType,
    summary: str,
    *,
    assignee_id: UUID | None = None,
    priority: int = 3,
) -> UUID:
    view = await IssueService(session, permissions).create(
        actor,
        NewIssue(
            project_id=project.id,
            type_id=issue_type.id,
            summary=summary,
            assignee_id=assignee_id,
            priority=priority,
        ),
    )
    return view.issue.id


async def transition_named(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    issue_id: UUID,
    name: str,
) -> UUID:
    available = await IssueService(session, permissions).available_transitions(actor, issue_id)
    return next(t.id for t in available if t.name == name)


class TestValidation:
    async def test_requires_board_manage(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            granted=(perms.ISSUE_VIEW,),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await BoardService(session, permissions).create(
                actor_for(user), project_id=project.id, name="B", columns=DEFAULT_COLUMNS
            )

    async def test_rejects_empty_columns(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="컬럼을 하나 이상"):
            await BoardService(session, permissions).create(
                actor, project_id=project.id, name="B", columns=[]
            )

    async def test_rejects_too_many_columns(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        columns = [{"name": f"C{i}", "iql": ""} for i in range(MAX_COLUMNS + 1)]
        with pytest.raises(ValidationError, match=f"{MAX_COLUMNS}개까지"):
            await BoardService(session, permissions).create(
                actor, project_id=project.id, name="B", columns=columns
            )

    async def test_rejects_duplicate_column_names(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="겹친다"):
            await BoardService(session, permissions).create(
                actor,
                project_id=project.id,
                name="B",
                columns=[{"name": "Same", "iql": ""}, {"name": "Same", "iql": ""}],
            )

    async def test_rejects_broken_column_iql_at_save_time(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        """저장할 때 잡지 않으면 보드를 여는 사람이 대신 터진다."""
        actor = await full_access(session, user, project)
        with pytest.raises(Exception, match="문법 오류"):
            await BoardService(session, permissions).create(
                actor,
                project_id=project.id,
                name="B",
                columns=[{"name": "Broken", "iql": "status ==== x"}],
            )

    async def test_rejects_bool_as_wip_limit(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        """bool 은 int 의 서브클래스다. True 가 WIP 1 로 새어 들어가면 안 된다."""
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="정수여야"):
            await BoardService(session, permissions).create(
                actor,
                project_id=project.id,
                name="B",
                columns=[{"name": "C", "iql": "", "wip_limit": True}],
            )

    async def test_rejects_zero_wip_limit(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="1 이상"):
            await BoardService(session, permissions).create(
                actor,
                project_id=project.id,
                name="B",
                columns=[{"name": "C", "iql": "", "wip_limit": 0}],
            )

    async def test_rejects_duplicate_board_name(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        await service.create(actor, project_id=project.id, name="B", columns=DEFAULT_COLUMNS)
        with pytest.raises(ConflictError):
            await service.create(actor, project_id=project.id, name="B", columns=DEFAULT_COLUMNS)


async def load_columns(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    board_id: UUID,
) -> list[ColumnResult]:
    """스윔레인이 없는 보드의 컬럼. 레인 하나만 오는 게 맞는지도 함께 본다."""
    lanes = (await BoardService(session, permissions).load(actor, board_id)).lanes
    assert [lane.key for lane in lanes] == [""]
    return lanes[0].columns


class TestLoad:
    async def test_splits_issues_by_column(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        todo = await make_issue(session, permissions, actor, project, issue_type, "todo one")
        moving = await make_issue(session, permissions, actor, project, issue_type, "in progress")
        await IssueService(session, permissions).transition(
            actor,
            moving,
            await transition_named(session, permissions, actor, moving, "Start progress"),
        )

        board = await BoardService(session, permissions).create(
            actor, project_id=project.id, name="Main", columns=DEFAULT_COLUMNS
        )
        columns = await load_columns(session, permissions, actor, board.id)

        assert [c.name for c in columns] == ["To Do", "In Progress", "Done"]
        assert [v.issue.id for v in columns[0].issues] == [todo]
        assert [v.issue.id for v in columns[1].issues] == [moving]
        assert columns[2].issues == []

    async def test_never_leaks_issues_from_another_project(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        other_project: Project,
        issue_type: IssueType,
    ) -> None:
        """컬럼 IQL 이 비어 있어도 보드는 자기 프로젝트만 본다.

        프로젝트 조건을 안 붙이면, 볼 권한이 있는 **다른** 프로젝트의 이슈가
        그대로 보드에 올라온다.
        """
        actor = await full_access(session, user, project, other_project)
        mine = await make_issue(session, permissions, actor, project, issue_type, "mine")
        await make_issue(session, permissions, actor, other_project, issue_type, "theirs")

        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="Everything",
            columns=[{"name": "All", "iql": ""}],
        )
        columns = await load_columns(session, permissions, actor, board.id)
        assert [v.issue.id for v in columns[0].issues] == [mine]

    async def test_hides_archived_issues(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issues = IssueService(session, permissions)
        alive = await make_issue(session, permissions, actor, project, issue_type, "alive")
        gone = await make_issue(session, permissions, actor, project, issue_type, "gone")
        await issues.archive(actor, gone)

        board = await BoardService(session, permissions).create(
            actor, project_id=project.id, name="Main", columns=[{"name": "All", "iql": ""}]
        )
        columns = await load_columns(session, permissions, actor, board.id)
        assert [v.issue.id for v in columns[0].issues] == [alive]

    async def test_flags_wip_breach(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        for i in range(3):
            await make_issue(session, permissions, actor, project, issue_type, f"#{i}")

        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="Main",
            columns=[{"name": "All", "iql": "", "wip_limit": 2}],
        )
        column = (await load_columns(session, permissions, actor, board.id))[0]
        assert column.loaded == 3
        assert column.over_wip is True
        assert column.truncated is False


class TestSwimlanes:
    async def test_no_swimlane_gives_one_blank_lane(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        await make_issue(session, permissions, actor, project, issue_type, "one")
        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="Plain",
            columns=[{"name": "All", "iql": ""}],
        )
        lanes = (await BoardService(session, permissions).load(actor, board.id)).lanes
        assert [(lane.key, lane.label) for lane in lanes] == [("", "")]

    async def test_splits_by_assignee_with_unassigned_last(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        await make_issue(
            session, permissions, actor, project, issue_type, "mine", assignee_id=user.id
        )
        await make_issue(session, permissions, actor, project, issue_type, "nobody's")

        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="By assignee",
            columns=[{"name": "All", "iql": ""}],
            swimlane_by="assignee",
        )
        lanes = (await BoardService(session, permissions).load(actor, board.id)).lanes

        # 담당자 없음은 맨 아래. 보통 아직 아무도 안 본 일이다.
        assert [lane.key for lane in lanes] == [str(user.id), "none"]
        assert lanes[0].label == user.display_name
        assert [v.issue.summary for v in lanes[0].columns[0].issues] == ["mine"]
        assert [v.issue.summary for v in lanes[1].columns[0].issues] == ["nobody's"]

    async def test_splits_by_priority_highest_first(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        await make_issue(session, permissions, actor, project, issue_type, "low", priority=5)
        await make_issue(session, permissions, actor, project, issue_type, "high", priority=1)

        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="By priority",
            columns=[{"name": "All", "iql": ""}],
            swimlane_by="priority",
        )
        lanes = (await BoardService(session, permissions).load(actor, board.id)).lanes
        # 1 이 가장 높다. 급한 것이 위로 온다.
        assert [lane.key for lane in lanes] == ["1", "5"]
        # 우선순위 이름("가장 높음" 등)은 번역 대상이라 서버가 정하지 않는다.
        # 대신 키를 그대로 준다 — 화면이 못 알아봐도 빈 줄로 보이진 않는다.
        assert [lane.label for lane in lanes] == ["1", "5"]

    async def test_empty_lanes_are_not_created(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """빈 줄만 늘어선 보드는 훑어보기 더 어렵다."""
        actor = await full_access(session, user, project)
        await make_issue(session, permissions, actor, project, issue_type, "only one", priority=2)
        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="Sparse",
            columns=[{"name": "All", "iql": ""}],
            swimlane_by="priority",
        )
        lanes = (await BoardService(session, permissions).load(actor, board.id)).lanes
        assert [lane.key for lane in lanes] == ["2"]

    async def test_wip_stays_column_wide(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """레인별로 다시 세면 나눠 놓은 것만으로 제한을 안 넘은 것처럼 보인다."""
        actor = await full_access(session, user, project)
        await make_issue(session, permissions, actor, project, issue_type, "a", priority=1)
        await make_issue(session, permissions, actor, project, issue_type, "b", priority=2)
        await make_issue(session, permissions, actor, project, issue_type, "c", priority=3)

        board = await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name="WIP",
            columns=[{"name": "All", "iql": "", "wip_limit": 2}],
            swimlane_by="priority",
        )
        lanes = (await BoardService(session, permissions).load(actor, board.id)).lanes
        assert len(lanes) == 3
        # 레인마다 카드는 하나뿐이지만 컬럼 전체로는 3장이라 제한을 넘었다.
        for lane in lanes:
            assert len(lane.columns[0].issues) == 1
            assert lane.columns[0].loaded == 3
            assert lane.columns[0].over_wip is True

    async def test_unknown_swimlane_field_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        """저장되면 보드를 열 때 터진다. 저장 시점에 막는다."""
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError) as exc:
            await BoardService(session, permissions).create(
                actor,
                project_id=project.id,
                name="Bad",
                columns=[{"name": "All", "iql": ""}],
                swimlane_by="epic",
            )
        assert exc.value.code == "issues.invalid_swimlane"

    async def test_swimlane_can_be_cleared(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        board = await service.create(
            actor,
            project_id=project.id,
            name="Toggle",
            columns=[{"name": "All", "iql": ""}],
            swimlane_by="assignee",
        )
        cleared = await service.update(actor, board.id, clear_swimlane=True)
        assert cleared.swimlane_by is None


class TestLoadPermissions:
    async def test_viewer_without_issue_view_cannot_open(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        owner = User(email=f"o-{new_id()}@example.com", display_name="Owner", status="active")
        session.add(owner)
        await session.flush()
        board = await BoardService(session, permissions).create(
            await full_access(session, owner, project),
            project_id=project.id,
            name="Main",
            columns=DEFAULT_COLUMNS,
        )
        with pytest.raises(PermissionDeniedError):
            await BoardService(session, permissions).get(actor_for(user), board.id)


class TestMove:
    async def test_runs_through_workflow_validation(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """보드 전용 전이 경로를 만들지 않는다. 없는 전이는 보드에서도 막힌다."""
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type, "x")
        board = await BoardService(session, permissions).create(
            actor, project_id=project.id, name="Main", columns=DEFAULT_COLUMNS
        )
        with pytest.raises(NotFoundError):
            await BoardService(session, permissions).move(actor, board.id, issue_id, new_id())

    async def test_moves_card(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        issue_id = await make_issue(session, permissions, actor, project, issue_type, "x")
        board = await BoardService(session, permissions).create(
            actor, project_id=project.id, name="Main", columns=DEFAULT_COLUMNS
        )
        start = await transition_named(session, permissions, actor, issue_id, "Start progress")
        view = await BoardService(session, permissions).move(actor, board.id, issue_id, start)
        assert view.state_category == "in_progress"

    async def test_rejects_issue_from_another_project(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        other_project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project, other_project)
        outsider = await make_issue(
            session, permissions, actor, other_project, issue_type, "outsider"
        )
        board = await BoardService(session, permissions).create(
            actor, project_id=project.id, name="Main", columns=DEFAULT_COLUMNS
        )
        start = await transition_named(session, permissions, actor, outsider, "Start progress")
        with pytest.raises(ValidationError, match="이 보드의 이슈가 아니다"):
            await BoardService(session, permissions).move(actor, board.id, outsider, start)


class TestUpdate:
    async def test_replaces_columns_and_clears_base_iql(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        board = await service.create(
            actor,
            project_id=project.id,
            name="Main",
            columns=DEFAULT_COLUMNS,
            base_iql="priority >= 3",
        )
        updated = await service.update(
            actor,
            board.id,
            name="Renamed",
            columns=[{"name": "Only", "iql": "statusCategory = todo"}],
            clear_base_iql=True,
        )
        assert updated.name == "Renamed"
        assert [c["name"] for c in updated.columns] == ["Only"]
        assert updated.base_iql is None

    async def test_rename_onto_existing_name_conflicts(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        await service.create(actor, project_id=project.id, name="A", columns=DEFAULT_COLUMNS)
        second = await service.create(
            actor, project_id=project.id, name="B", columns=DEFAULT_COLUMNS
        )
        with pytest.raises(ConflictError):
            await service.update(actor, second.id, name="A")

    async def test_rename_to_same_name_is_fine(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        board = await service.create(
            actor, project_id=project.id, name="A", columns=DEFAULT_COLUMNS
        )
        assert (await service.update(actor, board.id, name="A")).name == "A"

    async def test_delete(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        service = BoardService(session, permissions)
        board = await service.create(
            actor, project_id=project.id, name="A", columns=DEFAULT_COLUMNS
        )
        await service.delete(actor, board.id)
        await session.flush()
        with pytest.raises(NotFoundError):
            await service.get(actor, board.id)
