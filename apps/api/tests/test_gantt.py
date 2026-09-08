"""간트 (A17, M5). 실제 Postgres 를 쓴다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **모순된 의존을 조용히 그리지 않는다.** `A precedes B` 인데 B 가 먼저
  시작하면 일정 충돌이다. 겹친 막대만 그리면 사람은 화살표가 있으니 순서가
  지켜진다고 읽는다.
- **창 밖을 가리키는 의존은 그리지 않고 센다.** 권한을 확인하지 않은 이슈의
  날짜를 읽으면 안 보여야 할 일정이 새어 나간다.
- 막대는 먼저 시작하는 것부터. 순서가 시간이 아니면 화살표가 거슬러 올라간다.
- `blocks` 는 시간 순서가 아니라 상태 조건이라 화살표를 안 그린다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.gantt import GanttService
from ieum.modules.issues.models import (
    Issue,
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

pytestmark = pytest.mark.integration

TODAY = date(2026, 9, 15)
FROM = date(2026, 9, 1)
TO = date(2026, 9, 30)


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
    row = Project(key=f"G{secrets.token_hex(3).upper()}", name="Gantt Project")
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
    start: date | None = None,
    due: date | None = None,
) -> UUID:
    view = await IssueService(session, permissions).create(
        actor,
        NewIssue(project_id=project.id, type_id=issue_type.id, summary=summary),
    )
    if start is not None or due is not None:
        view.issue.start_date = start
        view.issue.due_date = due
        await session.flush()
    return view.issue.id


async def link(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    first: UUID,
    then: UUID,
    kind: str = "precedes",
) -> None:
    await IssueService(session, permissions).link(actor, first, then, kind)
    await session.flush()


async def gantt(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    **kw: object,
) -> object:
    return await GanttService(session, permissions).window(
        actor,
        project_id=project.id,
        starts_on=FROM,
        ends_on=TO,
        today=TODAY,
        **kw,  # type: ignore[arg-type]
    )


class TestConflicts:
    """**이 파일의 이유다.** 어긋난 의존을 겹친 막대로만 그리면 아무도 모른다."""

    async def test_a_successor_starting_too_early_is_a_conflict(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        first = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "먼저",
            start=date(2026, 9, 5),
            due=date(2026, 9, 12),
        )
        then = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "나중",
            start=date(2026, 9, 9),
            due=date(2026, 9, 15),
        )
        await link(session, permissions, actor, first, then)

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert len(view.conflicts) == 1
        conflict = view.conflicts[0]
        assert (conflict.predecessor, conflict.successor) == (first, then)
        # 12일에 끝나는데 9일에 시작한다 → 9·10·11·12 네 날이 겹친다.
        assert conflict.overlap_days == 4

    async def test_a_proper_order_is_not_a_conflict(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        first = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "먼저",
            start=date(2026, 9, 5),
            due=date(2026, 9, 8),
        )
        then = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "나중",
            start=date(2026, 9, 9),
            due=date(2026, 9, 12),
        )
        await link(session, permissions, actor, first, then)

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert view.conflicts == []
        # 화살표는 그대로 있다 — 충돌이 없다는 것이 의존이 없다는 뜻은 아니다.
        rows = {row.entry.id: row for row in view.rows}
        assert rows[then].depends_on == (first,)
        assert rows[first].depends_on == ()

    async def test_starting_the_day_the_predecessor_ends_counts_as_overlap(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """하루를 둘로 쪼개 쓰는 계획은 간트가 표현할 수 없다. "겹치지
        않는다" 고 말해 주면 그 사실이 숨는다."""
        first = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "먼저",
            start=date(2026, 9, 5),
            due=date(2026, 9, 9),
        )
        then = await make_issue(
            session, permissions, actor, project, issue_type, "나중", start=date(2026, 9, 9)
        )
        await link(session, permissions, actor, first, then)

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert [c.overlap_days for c in view.conflicts] == [1]


class TestOutsideTheWindow:
    async def test_a_link_pointing_outside_is_counted_not_drawn(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**권한 때문에** 그리지 않는다. 창 밖 이슈는 ACL 을 타고 오지
        않았으므로 날짜를 읽으면 안 보여야 할 일정이 새어 나간다."""
        inside = await make_issue(
            session, permissions, actor, project, issue_type, "안", due=date(2026, 9, 10)
        )
        far = await make_issue(
            session, permissions, actor, project, issue_type, "먼 뒤", due=date(2026, 12, 20)
        )
        await link(session, permissions, actor, inside, far)

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert [row.entry.id for row in view.rows] == [inside]
        assert view.links_outside == 1
        # 화살표도 충돌도 없다 — 셈만 남는다.
        assert view.rows[0].depends_on == ()
        assert view.conflicts == []

    async def test_undated_issues_are_counted_like_the_calendar(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        await make_issue(
            session, permissions, actor, project, issue_type, "놓임", due=date(2026, 9, 10)
        )
        await make_issue(session, permissions, actor, project, issue_type, "날짜 없음")

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert view.undated == 1


class TestOrder:
    async def test_bars_come_earliest_first(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """순서가 시간이 아니면 화살표가 아래에서 위로 거슬러 올라간다."""
        late = await make_issue(
            session, permissions, actor, project, issue_type, "늦게", due=date(2026, 9, 25)
        )
        early = await make_issue(
            session, permissions, actor, project, issue_type, "일찍", due=date(2026, 9, 3)
        )
        middle = await make_issue(
            session, permissions, actor, project, issue_type, "중간", due=date(2026, 9, 14)
        )

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert [row.entry.id for row in view.rows] == [early, middle, late]


class TestOnlyPrecedes:
    async def test_blocks_is_not_a_time_arrow(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """`blocks` 는 순서처럼 보이지만 **상태 조건**이다 — "이게 안 끝나면
        저걸 못 한다" 는 날짜와 별개다. 간트의 화살표는 시간축 위의
        화살표여야 한다."""
        first = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "막는 것",
            start=date(2026, 9, 5),
            due=date(2026, 9, 12),
        )
        then = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "막힌 것",
            start=date(2026, 9, 9),
            due=date(2026, 9, 15),
        )
        await link(session, permissions, actor, first, then, kind="blocks")

        view = await GanttService(session, permissions).window(
            actor, project_id=project.id, starts_on=FROM, ends_on=TO, today=TODAY
        )
        assert view.conflicts == []
        assert all(row.depends_on == () for row in view.rows)
        assert view.links_outside == 0
