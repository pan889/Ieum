"""스프린트와 번다운 (M5). 실제 Postgres 를 쓴다.

붙잡는 것 — 앞의 둘이 이 파일의 이유다:

- **번다운을 되짚어 계산하지 않는다.** 지금 상태로 과거를 그리면 어제 추가된
  이슈가 첫날부터 있었던 것이 되고, 범위가 늘어난 사실이 그림에서 사라진다.
- **닫을 때 남은 것을 잃지 않는다.** 어디로 보낼지 안 정하면 거절한다 —
  기본값을 두면 "다음에 하기로 했던 것" 이 조용히 백로그로 간다.
- **활성 스프린트는 하나다.** DB 가 막는다: 애플리케이션 검사만으로는 두
  요청이 동시에 시작할 때 둘 다 통과한다.
- **스프린트를 지워도 이슈는 남는다.**
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.boards import BoardContent, BoardService
from ieum.modules.issues.models import (
    Board,
    Issue,
    IssueType,
    SprintSnapshot,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.sprints import (
    SprintService,
    snapshot_sprints,
    totals_for,
    totals_of,
)
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role, RoleAssignment
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
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
    row = Project(key=f"S{secrets.token_hex(3).upper()}", name="Sprint Project")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def actor(session: AsyncSession, project: Project) -> Actor:
    user = User(email=f"u-{new_id()}@example.com", display_name="팀원", status="active")
    session.add(user)
    await session.flush()
    repo = RoleRepository(session)
    role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind="project")
    repo.add(role)
    await session.flush()
    for permission in perms.project_scoped():
        repo.grant(role.id, permission)
    repo.assign(
        role_id=role.id,
        scope=Scope.project(project.id),
        principal_kind="user",
        principal_id=user.id,
    )
    await session.flush()
    return Actor(user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow())


async def make_issue(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    issue_type: IssueType,
    summary: str,
    *,
    estimate_minutes: int | None = None,
) -> UUID:
    view = await IssueService(session, permissions).create(
        actor,
        NewIssue(project_id=project.id, type_id=issue_type.id, summary=summary),
    )
    if estimate_minutes is not None:
        # `NewIssue` 에는 추정이 없다(만들 때가 아니라 계획할 때 적는 값이다).
        # 시험은 값이 필요할 뿐이라 행에 바로 넣는다.
        view.issue.estimate_minutes = estimate_minutes
        await session.flush()
    return view.issue.id


async def finish(
    session: AsyncSession, permissions: PermissionService, actor: Actor, issue_id: UUID
) -> None:
    """done 분류까지 보낸다: Open → In Progress → Resolved."""
    service = IssueService(session, permissions)
    for name in ("Start progress", "Resolve"):
        available = await service.available_transitions(actor, issue_id)
        transition = next(t.id for t in available if t.name == name)
        await service.transition(actor, issue_id, transition)


class TestDefiningOne:
    async def test_create_and_list(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        service = SprintService(session, permissions)
        row = await service.create(actor, project_id=project.id, name="1주차", goal="첫 화면")
        assert row.state == "future"

        [view] = await service.list_for(actor, project.id)
        assert view.sprint.id == row.id
        assert view.totals.issues == 0

    async def test_a_duplicate_name_is_refused(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        service = SprintService(session, permissions)
        await service.create(actor, project_id=project.id, name="같은 이름")
        with pytest.raises(ConflictError):
            await service.create(actor, project_id=project.id, name="같은 이름")

    async def test_an_upside_down_window_is_refused(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        """끝이 시작보다 앞서면 번다운의 축이 거꾸로 간다."""
        now = utcnow()
        with pytest.raises(ValidationError) as exc:
            await SprintService(session, permissions).create(
                actor,
                project_id=project.id,
                name="거꾸로",
                starts_at=now,
                ends_at=now - timedelta(days=1),
            )
        assert exc.value.code == "issues.sprint_window_invalid"

    async def test_a_stranger_cannot_create(
        self, session: AsyncSession, permissions: PermissionService, project: Project
    ) -> None:
        nobody = User(email=f"n-{new_id()}@example.com", display_name="아무나", status="active")
        session.add(nobody)
        await session.flush()
        outsider = Actor(user_id=nobody.id, email=nobody.email, is_active=True)
        with pytest.raises(PermissionDeniedError):
            await SprintService(session, permissions).create(
                outsider, project_id=project.id, name="남의 것"
            )


class TestOnlyOneRuns:
    async def test_the_database_refuses_a_second_active(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        """**이 시험이 부분 유니크 인덱스를 지킨다.**

        애플리케이션에서만 막으면 두 요청이 동시에 시작할 때 둘 다 통과한다.
        """
        service = SprintService(session, permissions)
        first = await service.create(actor, project_id=project.id, name="첫째")
        second = await service.create(actor, project_id=project.id, name="둘째")
        await service.start(actor, first.id)

        with pytest.raises(ConflictError) as exc:
            await service.start(actor, second.id)
        assert exc.value.code == "issues.sprint_already_active"

    async def test_another_project_may_run_its_own(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        other = Project(key=f"T{secrets.token_hex(3).upper()}", name="다른 프로젝트")
        session.add(other)
        await session.flush()
        repo = RoleRepository(session)
        role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind="project")
        repo.add(role)
        await session.flush()
        for permission in perms.project_scoped():
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id,
            scope=Scope.project(other.id),
            principal_kind="user",
            principal_id=actor.user_id,
        )
        await session.flush()

        service = SprintService(session, permissions)
        mine = await service.create(actor, project_id=project.id, name="우리")
        theirs = await service.create(actor, project_id=other.id, name="저쪽")
        await service.start(actor, mine.id)
        # 프로젝트가 다르면 나란히 돈다 — 제약은 프로젝트 안에서만이다.
        assert (await service.start(actor, theirs.id)).state == "active"


class TestClosingIt:
    async def test_it_refuses_to_close_without_a_disposition(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**기본값을 두지 않는다.** 조용히 백로그로 흘려보내면 "다음에 하기로
        했던 것" 이 아무도 안 보는 곳으로 간다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="닫을 것")
        await service.start(actor, sprint.id)

        with pytest.raises(ValidationError) as exc:
            await service.close(actor, sprint.id, move_to=None)
        assert exc.value.code == "issues.sprint_needs_disposition"

    async def test_unfinished_issues_move_to_the_next_sprint(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        this_one = await service.create(actor, project_id=project.id, name="이번")
        next_one = await service.create(actor, project_id=project.id, name="다음")

        done = await make_issue(session, permissions, actor, project, issue_type, "끝낸 것")
        left = await make_issue(session, permissions, actor, project, issue_type, "남은 것")
        await service.assign_issues(
            actor, sprint_id=this_one.id, issue_ids=[done, left], project_id=project.id
        )
        await service.start(actor, this_one.id)
        await finish(session, permissions, actor, done)

        await service.close(actor, this_one.id, move_to=next_one.id)

        # 끝낸 것은 남고, 안 끝낸 것만 옮겨 간다 — 지난 스프린트의 성과를
        # 다음 스프린트가 가져가면 속도(velocity)가 거짓이 된다.
        assert (await session.get(Issue, done)).sprint_id == this_one.id
        assert (await session.get(Issue, left)).sprint_id == next_one.id

    async def test_to_backlog_is_a_choice_not_a_default(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="백로그로")
        left = await make_issue(session, permissions, actor, project, issue_type, "남은 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[left], project_id=project.id
        )
        await service.start(actor, sprint.id)
        await service.close(actor, sprint.id, move_to=None, to_backlog=True)
        assert (await session.get(Issue, left)).sprint_id is None

    async def test_it_refuses_two_dispositions(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="둘 다")
        other = await service.create(actor, project_id=project.id, name="저기")
        await service.start(actor, sprint.id)
        with pytest.raises(ValidationError) as exc:
            await service.close(actor, sprint.id, move_to=other.id, to_backlog=True)
        assert exc.value.code == "issues.sprint_disposition_ambiguous"

    async def test_a_closed_sprint_cannot_be_edited(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        """고치면 그때 찍힌 번다운과 어긋난다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="닫힌 것")
        await service.start(actor, sprint.id)
        await service.close(actor, sprint.id, move_to=None, to_backlog=True)
        with pytest.raises(ConflictError) as exc:
            await service.update(actor, sprint.id, name="새 이름")
        assert exc.value.code == "issues.sprint_closed"


class TestPuttingIssuesIn:
    async def test_in_and_out(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="담기")
        issue = await make_issue(session, permissions, actor, project, issue_type, "일감")

        assert (
            await service.assign_issues(
                actor, sprint_id=sprint.id, issue_ids=[issue], project_id=project.id
            )
            == 1
        )
        assert (await session.get(Issue, issue)).sprint_id == sprint.id

        # `None` 은 백로그다.
        await service.assign_issues(actor, sprint_id=None, issue_ids=[issue], project_id=project.id)
        assert (await session.get(Issue, issue)).sprint_id is None

    async def test_deleting_a_sprint_leaves_the_issues(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="지울 것")
        issue = await make_issue(session, permissions, actor, project, issue_type, "살아남을 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[issue], project_id=project.id
        )

        await service.delete(actor, sprint.id)
        row = await session.get(Issue, issue)
        assert row is not None
        assert row.sprint_id is None

    async def test_a_running_sprint_cannot_be_deleted(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor, project: Project
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="도는 중")
        await service.start(actor, sprint.id)
        with pytest.raises(ConflictError) as exc:
            await service.delete(actor, sprint.id)
        assert exc.value.code == "issues.sprint_active"


class TestTheBurndown:
    async def test_starting_writes_the_first_point(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """안 찍으면 하루짜리 스프린트의 번다운이 통째로 빈다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="첫 점")
        issue = await make_issue(
            session, permissions, actor, project, issue_type, "일감", estimate_minutes=120
        )
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[issue], project_id=project.id
        )
        await service.start(actor, sprint.id)

        [point] = await service.burndown(actor, sprint.id)
        assert point.remaining_issues == 1
        assert point.remaining_minutes == 120
        assert point.total_issues == 1

    async def test_the_point_stays_live_through_the_day(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """오늘 줄은 덮어쓴다 — 하루에 여러 점이 생기지 않는다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="오늘")
        issue = await make_issue(session, permissions, actor, project, issue_type, "일감")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[issue], project_id=project.id
        )
        await service.start(actor, sprint.id)
        await finish(session, permissions, actor, issue)
        await snapshot_sprints(session)

        points = await service.burndown(actor, sprint.id)
        assert len(points) == 1
        assert points[0].remaining_issues == 0

    async def test_yesterdays_point_does_not_move(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이 시험이 이 파일의 이유다.**

        되짚어 계산하면 어제 값이 오늘 상태로 다시 그려지고, 그 사이에
        일어난 일이 그림에서 사라진다.
        """
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="어제와 오늘")
        first = await make_issue(session, permissions, actor, project, issue_type, "어제 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[first], project_id=project.id
        )
        await service.start(actor, sprint.id)

        # 어제 찍힌 것으로 만든다.
        yesterday = (utcnow() - timedelta(days=1)).date()
        row = (
            await session.execute(
                select(SprintSnapshot).where(SprintSnapshot.sprint_id == sprint.id)
            )
        ).scalar_one()
        row.on_date = yesterday
        await session.flush()

        # 오늘 이슈가 하나 더 들어오고, 어제 것을 끝낸다.
        second = await make_issue(session, permissions, actor, project, issue_type, "오늘 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[second], project_id=project.id
        )
        await finish(session, permissions, actor, first)
        await snapshot_sprints(session)

        points = await service.burndown(actor, sprint.id)
        assert len(points) == 2
        # 어제 점은 그때 그대로: 하나 있었고 하나 남아 있었다.
        assert (points[0].total_issues, points[0].remaining_issues) == (1, 1)
        # 오늘 점은 둘 중 하나 남았다 — **전체가 늘어난 것이 보인다.**
        assert (points[1].total_issues, points[1].remaining_issues) == (2, 1)

    async def test_closing_records_the_last_point_before_moving(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """옮기고 나서 찍으면 남은 것이 0 이 되고, 완주한 것처럼 보인다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="미완주")
        left = await make_issue(session, permissions, actor, project, issue_type, "못 끝낸 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[left], project_id=project.id
        )
        await service.start(actor, sprint.id)
        await service.close(actor, sprint.id, move_to=None, to_backlog=True)

        points = await service.burndown(actor, sprint.id)
        assert points[-1].remaining_issues == 1

    async def test_a_closed_sprint_is_not_touched_again(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """지난 기록이 바뀌면 안 된다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="닫힘")
        await service.start(actor, sprint.id)
        await service.close(actor, sprint.id, move_to=None, to_backlog=True)

        before = await service.burndown(actor, sprint.id)
        assert await snapshot_sprints(session) == 0
        assert await service.burndown(actor, sprint.id) == before


class TestCounting:
    async def test_issues_without_an_estimate_count_as_zero(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """`NULL` 을 빼면 추정 안 한 이슈가 많은 스프린트가 가벼워 보인다 —
        개수 축이 그걸 말해 준다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="세기")
        sized = await make_issue(
            session, permissions, actor, project, issue_type, "추정함", estimate_minutes=60
        )
        unsized = await make_issue(session, permissions, actor, project, issue_type, "추정 안 함")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[sized, unsized], project_id=project.id
        )

        found = await totals_of(session, sprint.id)
        assert (found.issues, found.minutes) == (2, 60)

    async def test_an_issue_from_another_project_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """부분 성공으로 두면 부르는 쪽은 무엇이 안 들어갔는지 모른다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="남의 이슈")
        with pytest.raises(ValidationError) as exc:
            await service.assign_issues(
                actor, sprint_id=sprint.id, issue_ids=[new_id()], project_id=project.id
            )
        assert exc.value.code == "issues.sprint_issue_foreign"

    async def test_a_missing_sprint_is_not_found(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor
    ) -> None:
        with pytest.raises(NotFoundError):
            await SprintService(session, permissions).burndown(actor, new_id())


class TestFindingThemWithIql:
    async def test_backlog_and_sprint_and_state(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """`sprint IS EMPTY` 가 백로그다. "지금 도는 것" 은 상태로 묻는다 —
        마법 값을 두면 저장한 필터가 시간이 지나 다른 것을 가리킨다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="이번 주")
        inside = await make_issue(session, permissions, actor, project, issue_type, "안에 있는 것")
        outside = await make_issue(session, permissions, actor, project, issue_type, "백로그")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[inside], project_id=project.id
        )
        await service.start(actor, sprint.id)

        async def ids(iql: str) -> list[UUID]:
            page = await SearchService(session, permissions).search(
                actor, iql, PageRequest(limit=50)
            )
            return [row.id for row in page.items]

        assert await ids(f"project = {project.key} AND sprint IS EMPTY") == [outside]
        assert await ids(f"project = {project.key} AND sprintstate = active") == [inside]
        assert await ids(f'project = {project.key} AND sprint = "이번 주"') == [inside]

    async def test_not_equal_includes_the_backlog(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**바깥 조인이라 백로그가 살아 있다.**

        안쪽으로 걸면 `sprint != "..."` 가 "다른 스프린트에 있는 것" 만
        뜻하게 되는데, 사람이 기대하는 것은 "그게 아닌 것 전부" 다.
        """
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="어떤 스프린트")
        inside = await make_issue(session, permissions, actor, project, issue_type, "안")
        outside = await make_issue(session, permissions, actor, project, issue_type, "밖")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[inside], project_id=project.id
        )

        page = await SearchService(session, permissions).search(
            actor,
            f'project = {project.key} AND sprint != "어떤 스프린트"',
            PageRequest(limit=50),
        )
        assert [row.id for row in page.items] == [outside]


class TestTheBoardKnows:
    """보드가 스프린트를 안다 (M5).

    스프린트가 생기면 보드는 "열려 있는 것 전부" 가 아니라 **이번 주기**를
    뜻해야 한다. 다만 스프린트를 아직 안 쓰는 팀의 보드가 텅 비면 안 된다.
    """

    @staticmethod
    async def _board(
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        **kwargs: object,
    ) -> Board:
        return await BoardService(session, permissions).create(
            actor,
            project_id=project.id,
            name=f"보드-{secrets.token_hex(3)}",
            columns=[{"name": "전부", "iql": "", "wip_limit": None}],
            **kwargs,  # type: ignore[arg-type]
        )

    @staticmethod
    def _keys(content: BoardContent) -> set[UUID]:
        return {
            view.issue.id
            for lane in content.lanes
            for column in lane.columns
            for view in column.issues
        }

    async def test_an_active_sprint_narrows_the_board(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="이번 주기")
        inside = await make_issue(session, permissions, actor, project, issue_type, "안")
        await make_issue(session, permissions, actor, project, issue_type, "백로그")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[inside], project_id=project.id
        )
        await service.start(actor, sprint.id)

        board = await self._board(session, permissions, actor, project)
        content = await BoardService(session, permissions).load(actor, board.id)

        assert self._keys(content) == {inside}
        # **무엇으로 걸렀는지 응답에 담긴다.** 안 담으면 화면은 백로그가
        # 비었는지 걸러졌는지 구분할 수 없다.
        assert content.sprint is not None
        assert content.sprint.name == "이번 주기"

    async def test_without_an_active_sprint_it_shows_everything(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**빈 보드를 띄우지 않는다.**

        거를 것이 없으면 거르지 않는다. 스프린트를 아직 안 쓰는 팀에게
        `sprint_mode = "active"` 가 "보드 고장" 으로 보이면 안 된다.
        """
        one = await make_issue(session, permissions, actor, project, issue_type, "하나")
        two = await make_issue(session, permissions, actor, project, issue_type, "둘")

        board = await self._board(session, permissions, actor, project)
        content = await BoardService(session, permissions).load(actor, board.id)

        assert self._keys(content) == {one, two}
        # 걸지 않았다는 사실이 그대로 온다 — 화면은 이걸 보고 안내를 띄운다.
        assert content.sprint is None

    async def test_a_future_sprint_does_not_count(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """계획만 세운 스프린트는 보드를 바꾸지 않는다. 시작해야 시작이다."""
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="다음 주기")
        planned = await make_issue(session, permissions, actor, project, issue_type, "계획")
        loose = await make_issue(session, permissions, actor, project, issue_type, "백로그")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[planned], project_id=project.id
        )

        board = await self._board(session, permissions, actor, project)
        content = await BoardService(session, permissions).load(actor, board.id)

        assert self._keys(content) == {planned, loose}
        assert content.sprint is None

    async def test_mode_all_ignores_the_sprint(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="도는 것")
        inside = await make_issue(session, permissions, actor, project, issue_type, "안")
        outside = await make_issue(session, permissions, actor, project, issue_type, "밖")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[inside], project_id=project.id
        )
        await service.start(actor, sprint.id)

        board = await self._board(session, permissions, actor, project, sprint_mode="all")
        content = await BoardService(session, permissions).load(actor, board.id)

        assert self._keys(content) == {inside, outside}
        assert content.sprint is None

    async def test_a_quote_in_the_name_does_not_break_the_board(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이름은 사람이 짓는다.**

        보드는 스프린트 이름을 IQL 문자열에 끼워 넣는다. 프로젝트 키와 달리
        형식을 강제할 수 없는 값이라, 따옴표가 들어오면 그대로는 질의가
        깨지거나 — 더 나쁘게는 — 다른 조건이 된다.
        """
        name = 'He said "go" \\ now'
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name=name)
        inside = await make_issue(session, permissions, actor, project, issue_type, "안")
        await make_issue(session, permissions, actor, project, issue_type, "밖")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[inside], project_id=project.id
        )
        await service.start(actor, sprint.id)

        board = await self._board(session, permissions, actor, project)
        content = await BoardService(session, permissions).load(actor, board.id)

        assert self._keys(content) == {inside}
        assert content.sprint is not None
        assert content.sprint.name == name

    async def test_an_unknown_mode_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        with pytest.raises(ValidationError):
            await self._board(session, permissions, actor, project, sprint_mode="이번주만")


class TestClearingFields:
    """`null` 로는 못 지운다 — `clear_*` 로 말한다.

    JSON 에는 "값 없음" 과 `null` 을 가릴 방법이 없다. 둘을 같게 다루면 기간을
    지우려는 요청이 **조용히 아무 일도 안 하고 성공한다** — 화면은 지워진 줄
    알고, 다음에 열면 그대로 있다.
    """

    async def test_none_leaves_the_value_alone(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(
            actor,
            project_id=project.id,
            name="기간 있는 것",
            goal="목표",
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=14),
        )
        # 이름만 고친다. 나머지는 `None` 이지만 지워지지 않는다.
        await service.update(actor, sprint.id, name="이름만 고침")
        assert sprint.goal == "목표"
        assert sprint.starts_at is not None
        assert sprint.ends_at is not None

    async def test_clear_flags_actually_clear(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        service = SprintService(session, permissions)
        sprint = await service.create(
            actor,
            project_id=project.id,
            name="지울 것",
            goal="목표",
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=14),
        )
        await service.update(
            actor, sprint.id, clear_goal=True, clear_starts_at=True, clear_ends_at=True
        )
        assert sprint.goal is None
        assert sprint.starts_at is None
        assert sprint.ends_at is None


class TestArchivedIssues:
    async def test_closing_leaves_archived_issues_behind(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**아카이브된 것은 두고 간다.**

        닫기 화면이 말한 "N 건 남았다" 는 `totals_of` 값이고, 그건 아카이브를
        안 센다. 옮기는 수가 그보다 많으면 말과 행동이 어긋난다. 그리고
        아카이브된 이슈가 있던 곳은 닫힌 그 스프린트다 — 되살릴 때 그 사실이
        남아 있는 것이 맞다.
        """
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="닫을 것)")
        nextone = await service.create(actor, project_id=project.id, name="다음")
        live = await make_issue(session, permissions, actor, project, issue_type, "살아 있는 것")
        gone = await make_issue(session, permissions, actor, project, issue_type, "아카이브된 것")
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[live, gone], project_id=project.id
        )
        await service.start(actor, sprint.id)

        archived = await session.get(Issue, gone)
        assert archived is not None
        archived.archived_at = utcnow()
        await session.flush()

        await service.close(actor, sprint.id, move_to=nextone.id)

        moved = await session.get(Issue, live)
        assert moved is not None
        assert moved.sprint_id == nextone.id
        # 아카이브된 것은 그 자리에 남는다.
        assert archived.sprint_id == sprint.id


class TestListOrder:
    async def test_running_first_then_planned_then_closed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        """**`state` 로 그냥 정렬하면 안 된다.**

        알파벳순은 active, closed, future 라 끝난 스프린트가 예정된 것 앞에
        온다 — 계획 회의에서 보는 화면인데 지난 것이 다음 것을 가린다.
        """
        service = SprintService(session, permissions)
        done = await service.create(actor, project_id=project.id, name="지난 주기")
        running = await service.create(actor, project_id=project.id, name="이번 주기")
        planned = await service.create(actor, project_id=project.id, name="다음 주기")

        await service.start(actor, done.id)
        await service.close(actor, done.id, move_to=None, to_backlog=True)
        await service.start(actor, running.id)

        names = [view.sprint.name for view in await service.list_for(actor, project.id)]
        assert names == [running.name, planned.name, done.name]


async def _grant(session: AsyncSession, actor: Actor, project: Project) -> RoleAssignment:
    """이 액터에게 그 프로젝트의 이슈 권한을 준다. **할당을 돌려준다** —
    되돌리는 시험이 그것을 지운다."""
    repo = RoleRepository(session)
    role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind="project")
    repo.add(role)
    await session.flush()
    for permission in perms.project_scoped():
        repo.grant(role.id, permission)
    assignment = repo.assign(
        role_id=role.id,
        scope=Scope.project(project.id),
        principal_kind="user",
        principal_id=actor.user_id,
    )
    await session.flush()
    return assignment


async def _other_project(session: AsyncSession, actor: Actor, name: str) -> Project:
    row = Project(key=f"T{secrets.token_hex(3).upper()}", name=name)
    session.add(row)
    await session.flush()
    await _grant(session, actor, row)
    return row


async def _put_my_work_in(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    issue_type: IssueType,
    sprint_id: UUID,
    summary: str = "내 일",
) -> UUID:
    """그 스프린트에 **내게 배정된** 이슈 하나를 넣는다.

    `list_mine` 은 내 일이 든 스프린트만 보므로 시험마다 이것이 있어야 한다 —
    없으면 전부 빈 목록이고, 그러면 이 목록의 다른 규칙(권한·접힘·정렬)을
    아무것도 안 보는 시험이 된다.
    """
    view = await IssueService(session, permissions).create(
        actor,
        NewIssue(
            project_id=project.id,
            type_id=issue_type.id,
            summary=summary,
            assignee_id=actor.user_id,
        ),
    )
    await SprintService(session, permissions).assign_issues(
        actor, sprint_id=sprint_id, issue_ids=[view.issue.id], project_id=project.id
    )
    return view.issue.id


class TestMySprints:
    """첫 화면이 쓰는 목록 (M5 대시보드).

    **프로젝트를 고르지 않는 목록이다.** 그래서 `list_for` 가 안 보던 것들을
    여기서 처음 본다: 남의 프로젝트, 접힌 프로젝트, "볼 수 있는 것이 하나도
    없는 사람", 그리고 **내 일이 안 든 스프린트**.
    """

    async def test_it_crosses_projects(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        other = await _other_project(session, actor, "다른 프로젝트")
        service = SprintService(session, permissions)
        mine = await service.create(actor, project_id=project.id, name="우리 주기")
        theirs = await service.create(actor, project_id=other.id, name="저쪽 주기")
        await service.start(actor, mine.id)
        await service.start(actor, theirs.id)
        await _put_my_work_in(session, permissions, actor, project, issue_type, mine.id)
        await _put_my_work_in(session, permissions, actor, other, issue_type, theirs.id)

        found = await service.list_mine(actor)
        names = {row.view.sprint.name for row in found}
        assert {"우리 주기", "저쪽 주기"} <= names
        # **프로젝트가 함께 온다.** 여러 프로젝트를 섞어 보여 주는 화면이라
        # 이름만 있으면 그게 누구의 주기인지 알 수 없다.
        keys = {row.view.sprint.name: row.project.key for row in found}
        assert keys["우리 주기"] == project.key
        assert keys["저쪽 주기"] == other.key

    async def test_only_the_running_ones(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = SprintService(session, permissions)
        planned = await service.create(actor, project_id=project.id, name="예정")
        done = await service.create(actor, project_id=project.id, name="지난")
        await _put_my_work_in(
            session, permissions, actor, project, issue_type, planned.id, "예정 일"
        )
        await service.start(actor, done.id)
        await _put_my_work_in(session, permissions, actor, project, issue_type, done.id, "지난 일")
        await service.close(actor, done.id, move_to=None, to_backlog=True)

        names = {row.view.sprint.name for row in await service.list_mine(actor)}
        assert planned.name not in names
        assert done.name not in names

    async def test_an_archived_project_drops_out(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**프로젝트를 접는 것은 "이제 안 본다" 는 뜻이다.**

        그 안에서 돌던 스프린트가 첫 화면에 남아 있으면 접은 일이 안 된 것처럼
        보이고, 사람은 스프린트를 하나씩 닫으러 들어간다.
        """
        service = SprintService(session, permissions)
        running = await service.create(actor, project_id=project.id, name="접힐 주기")
        await service.start(actor, running.id)
        await _put_my_work_in(session, permissions, actor, project, issue_type, running.id)
        assert {row.view.sprint.name for row in await service.list_mine(actor)} == {"접힐 주기"}

        project.archived_at = utcnow()
        await session.flush()

        assert await service.list_mine(actor) == []

    async def test_a_stranger_sees_nothing(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """역할이 없는 사람에게는 **질의를 보내지도 않는다**(`acl.is_empty`).

        빈 목록이 나오는 것과 남의 스프린트 이름이 보이는 것 사이에는 사고가
        하나 있다 — 이름만으로도 새는 것이 있다.
        """
        service = SprintService(session, permissions)
        running = await service.create(actor, project_id=project.id, name="남의 주기")
        await service.start(actor, running.id)
        await _put_my_work_in(session, permissions, actor, project, issue_type, running.id)

        outsider = User(email=f"x-{new_id()}@example.com", display_name="외부", status="active")
        session.add(outsider)
        await session.flush()
        stranger = Actor(user_id=outsider.id, email=outsider.email, is_active=True)

        assert await service.list_mine(stranger) == []

    async def test_it_hides_my_own_work_in_a_project_i_lost(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이 시험이 이 목록의 권한을 지킨다.**

        "내 일이 든 스프린트" 로 좁히고 나면 남의 프로젝트는 대개 자동으로
        빠진다 — 남의 일이 배정된 곳이니까. 그래서 남의 프로젝트로 시험하면
        **ACL 필터를 지워도 초록이다.**

        정말 필요한 자리는 이쪽이다: 팀을 옮겨 그 프로젝트의 역할을 잃었는데
        **옛 이슈에는 내 이름이 그대로 남아 있다.** 그러면 `EXISTS` 는 걸리고,
        막는 것은 ACL 뿐이다. 스프린트 이름은 팀이 하는 일을 말하므로
        ("결제 이관 2주차") 이름만으로도 새는 것이 있다.
        """
        left = Project(key=f"V{secrets.token_hex(3).upper()}", name="떠난 프로젝트")
        session.add(left)
        await session.flush()
        assignment = await _grant(session, actor, left)

        service = SprintService(session, permissions)
        gone = await service.create(actor, project_id=left.id, name="떠난 주기")
        await service.start(actor, gone.id)
        await _put_my_work_in(session, permissions, actor, left, issue_type, gone.id)
        mine = await service.create(actor, project_id=project.id, name="남은 주기")
        await service.start(actor, mine.id)
        await _put_my_work_in(session, permissions, actor, project, issue_type, mine.id)

        # 역할을 잃는다. **액터를 새로 만든다** — ACL 은 액터에 캐시되므로
        # 같은 객체로 다시 물으면 잃기 전의 답이 돌아온다.
        await RoleRepository(session).delete_assignment(assignment.id)
        await session.flush()
        moved = Actor(
            user_id=actor.user_id, email=actor.email, is_active=True, mfa_satisfied_at=utcnow()
        )

        names = [row.view.sprint.name for row in await service.list_mine(moved)]
        assert names == ["남은 주기"]

    async def test_it_sorts_by_when_they_end(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """곧 끝나는 것이 위다. **기한 없는 것은 맨 뒤**로 — 앞에 오면 이
        목록을 보는 이유가 사라진다."""
        later = await _other_project(session, actor, "나중")
        never = await _other_project(session, actor, "무기한")
        service = SprintService(session, permissions)
        soon_row = await service.create(
            actor,
            project_id=project.id,
            name="곧 끝",
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=1),
        )
        later_row = await service.create(
            actor,
            project_id=later.id,
            name="나중 끝",
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=30),
        )
        never_row = await service.create(actor, project_id=never.id, name="기한 없음")
        for row, where in ((soon_row, project), (later_row, later), (never_row, never)):
            await service.start(actor, row.id)
            await _put_my_work_in(session, permissions, actor, where, issue_type, row.id)

        names = [row.view.sprint.name for row in await service.list_mine(actor)]
        assert names == ["곧 끝", "나중 끝", "기한 없음"]

    async def test_a_sprint_without_my_work_does_not_show(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이 시험이 이 목록을 "내 일" 로 만든다.**

        도는 스프린트 전부를 보여 주면, 팀이 열인 설치에서는 다섯 칸이 임의로
        고른 남의 주기로 찬다 — 개발 DB 에 서른여섯 개가 쌓인 날 그게
        드러났다. 첫 화면은 그 자리를 그렇게 쓸 수 없다.
        """
        service = SprintService(session, permissions)
        empty = await service.create(actor, project_id=project.id, name="내 일 없는 주기")
        await service.start(actor, empty.id)
        # 스프린트에 이슈는 있지만 **내게 배정되지 않았다.**
        someone_else = User(email=f"e-{new_id()}@example.com", display_name="남", status="active")
        session.add(someone_else)
        await session.flush()
        view = await IssueService(session, permissions).create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="남의 일",
                assignee_id=someone_else.id,
            ),
        )
        await service.assign_issues(
            actor, sprint_id=empty.id, issue_ids=[view.issue.id], project_id=project.id
        )

        assert await service.list_mine(actor) == []

    async def test_counting_an_empty_sprint_gives_zero(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        """**`GROUP BY` 는 이슈가 없는 스프린트에 행을 주지 않는다.**

        묶어 세는 질의를 그대로 쓰면 그 스프린트가 목록에서 사라지거나
        `KeyError` 로 첫 화면이 통째로 안 뜬다. 0 으로 채워 돌려준다.
        """
        sprint = await SprintService(session, permissions).create(
            actor, project_id=project.id, name="빈 주기"
        )
        found = await totals_for(session, [sprint.id])
        assert found[sprint.id].issues == 0
        assert found[sprint.id].remaining_issues == 0
        assert found[sprint.id].minutes == 0

    async def test_batched_counting_matches_the_single_one(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**세는 규칙은 한 곳에만 있어야 한다.**

        한 개짜리와 여러 개짜리가 각자 질의를 들면 언젠가 한쪽만 고쳐지고,
        그때 목록의 숫자와 상세의 숫자가 달라진다 — 어느 쪽이 맞는지는 아무도
        모른다.
        """
        service = SprintService(session, permissions)
        sprint = await service.create(actor, project_id=project.id, name="세는 주기")
        first = await make_issue(
            session, permissions, actor, project, issue_type, "하나", estimate_minutes=60
        )
        second = await make_issue(
            session, permissions, actor, project, issue_type, "둘", estimate_minutes=30
        )
        await service.assign_issues(
            actor, sprint_id=sprint.id, issue_ids=[first, second], project_id=project.id
        )
        await finish(session, permissions, actor, second)

        one = await totals_of(session, sprint.id)
        many = await totals_for(session, [sprint.id])
        assert many[sprint.id] == one
        assert one.issues == 2
        assert one.remaining_issues == 1
        assert one.minutes == 90
        assert one.remaining_minutes == 60
