"""달력 (A18, M5). 실제 Postgres 를 쓴다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **날짜가 없는 이슈를 조용히 빼지 않는다.** 몇 건이 안 보이는지 응답이
  말한다. 안 그러면 사람은 "이번 주는 비어 있다" 고 읽는데 실제로는 날짜만
  안 적힌 일이 스무 건 있다.
- **SQL 의 겹침 판정과 화면의 놓는 규칙이 같다.** 한쪽만 고치면 질의에는
  걸리는데 놓을 자리가 없는 이슈가 생긴다.
- **시작만 있으면 하루다.** 오늘까지 늘리면 매일 길어지는 띠가 된다.
- 창에는 상한이 있다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import date, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.calendar import MAX_DAYS, CalendarService, span_of
from ieum.modules.issues.models import (
    Issue,
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.sprints import SprintService
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

pytestmark = pytest.mark.integration

TODAY = date(2026, 9, 15)
MONTH_START = date(2026, 9, 1)
MONTH_END = date(2026, 9, 30)


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
    row = Project(key=f"C{secrets.token_hex(3).upper()}", name="Calendar Project")
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


class TestUndatedIsCounted:
    """**조용히 빼지 않는다.** 이 파일의 이유다."""

    async def test_issues_without_dates_are_counted_not_dropped(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        placed = await make_issue(
            session, permissions, actor, project, issue_type, "놓인 것", due=date(2026, 9, 10)
        )
        for i in range(3):
            await make_issue(session, permissions, actor, project, issue_type, f"날짜 없음 {i}")

        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        assert [e.id for e in view.entries] == [placed]
        # 빠진 셋이 숫자로 남는다. 이걸 안 세면 화면은 "9월은 한 건" 이라고
        # 말하는데 실제로는 넷이다.
        assert view.undated == 3

    async def test_the_count_respects_the_extra_filter(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """좁힌 질의에 안 걸리는 이슈는 세지도 않는다 — 안 그러면 "안 보이는
        것" 이 필터와 무관하게 늘어나서 숫자가 거짓말을 한다."""
        await make_issue(session, permissions, actor, project, issue_type, "날짜 없는 낮은 것")
        await make_issue(session, permissions, actor, project, issue_type, "날짜 없는 급한 것")
        rows = list((await session.execute(_all_issues(project))).scalars().all())
        for row in rows:
            if "급한" in row.summary:
                row.priority = 1
        await session.flush()

        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            iql="priority = 1",
            today=TODAY,
        )
        assert view.undated == 1


class TestPlacing:
    async def test_both_dates_make_a_band(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        issue = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "띠",
            start=date(2026, 9, 7),
            due=date(2026, 9, 11),
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        entry = next(e for e in view.entries if e.id == issue)
        assert (entry.starts_on, entry.ends_on) == (date(2026, 9, 7), date(2026, 9, 11))

    async def test_only_a_start_is_one_day(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**언제 끝나는지 모른다.** 오늘까지 늘리면 매일 길어지는 띠가 되고,
        끝없이 늘리면 달력이 그 하나로 덮인다."""
        issue = await make_issue(
            session, permissions, actor, project, issue_type, "시작만", start=date(2026, 9, 3)
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        entry = next(e for e in view.entries if e.id == issue)
        assert (entry.starts_on, entry.ends_on) == (date(2026, 9, 3), date(2026, 9, 3))

    async def test_upside_down_dates_are_shown_not_hidden(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """사람이 마감을 시작보다 앞에 적을 수 있다. 빼 버리면 **잘못 적힌
        날짜를 고칠 기회조차** 화면에 안 뜬다."""
        issue = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "거꾸로",
            start=date(2026, 9, 20),
            due=date(2026, 9, 10),
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        entry = next(e for e in view.entries if e.id == issue)
        assert (entry.starts_on, entry.ends_on) == (date(2026, 9, 10), date(2026, 9, 20))

    async def test_a_band_crossing_the_window_edge_still_shows(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """지난달에 시작해서 이번 달에 끝나는 일은 이번 달 달력에 있어야
        한다. 시작만 보고 거르면 사라진다."""
        issue = await make_issue(
            session,
            permissions,
            actor,
            project,
            issue_type,
            "걸쳐 있는 것",
            start=date(2026, 8, 25),
            due=date(2026, 9, 4),
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        assert issue in [e.id for e in view.entries]

    async def test_outside_the_window_is_not_included(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        await make_issue(
            session, permissions, actor, project, issue_type, "다음 달", due=date(2026, 10, 5)
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        assert view.entries == []
        assert view.undated == 0


class TestOverdue:
    async def test_a_passed_deadline_is_marked(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        late = await make_issue(
            session, permissions, actor, project, issue_type, "늦음", due=date(2026, 9, 10)
        )
        soon = await make_issue(
            session, permissions, actor, project, issue_type, "아직", due=date(2026, 9, 20)
        )
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        flags = {e.id: e.overdue for e in view.entries}
        assert flags[late] is True
        assert flags[soon] is False

    async def test_a_finished_issue_is_never_overdue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """끝난 일에 빨간 표시를 남기면 달력이 지난 일로 가득 찬다."""
        issue = await make_issue(
            session, permissions, actor, project, issue_type, "끝났음", due=date(2026, 9, 5)
        )
        service = IssueService(session, permissions)
        for name in ("Start progress", "Resolve"):
            available = await service.available_transitions(actor, issue)
            await service.transition(actor, issue, next(t.id for t in available if t.name == name))

        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        entry = next(e for e in view.entries if e.id == issue)
        assert entry.overdue is False


class TestSprintBands:
    async def test_a_sprint_with_a_window_lands_on_the_calendar(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        sprints = SprintService(session, permissions)
        inside = await sprints.create(
            actor,
            project_id=project.id,
            name="이번 주기",
            starts_at=utcnow().replace(year=2026, month=9, day=7),
            ends_at=utcnow().replace(year=2026, month=9, day=18),
        )
        await sprints.create(
            actor,
            project_id=project.id,
            name="한참 뒤",
            starts_at=utcnow().replace(year=2026, month=12, day=1),
            ends_at=utcnow().replace(year=2026, month=12, day=14),
        )
        # 기간을 안 적은 스프린트는 놓을 자리가 없다.
        await sprints.create(actor, project_id=project.id, name="언제일지 모름")

        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_END,
            today=TODAY,
        )
        assert [s.id for s in view.sprints] == [inside.id]
        # **시각 그대로** 온다. 날짜로 접는 것은 화면 몫이다.
        assert view.sprints[0].starts_at is not None


class TestTheWindow:
    async def test_an_upside_down_window_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        with pytest.raises(ValidationError):
            await CalendarService(session, permissions).window(
                actor,
                project_id=project.id,
                starts_on=MONTH_END,
                ends_on=MONTH_START,
            )

    async def test_too_wide_a_window_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        with pytest.raises(ValidationError):
            await CalendarService(session, permissions).window(
                actor,
                project_id=project.id,
                starts_on=MONTH_START,
                ends_on=MONTH_START + timedelta(days=MAX_DAYS),
            )

    async def test_exactly_the_limit_is_allowed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
    ) -> None:
        view = await CalendarService(session, permissions).window(
            actor,
            project_id=project.id,
            starts_on=MONTH_START,
            ends_on=MONTH_START + timedelta(days=MAX_DAYS - 1),
            today=TODAY,
        )
        assert view.entries == []

    async def test_a_stranger_cannot_read_it(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        project: Project,
    ) -> None:
        stranger = User(email=f"s-{new_id()}@example.com", display_name="남", status="active")
        session.add(stranger)
        await session.flush()
        outsider = Actor(
            user_id=stranger.id,
            email=stranger.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        with pytest.raises(PermissionDeniedError):
            await CalendarService(session, permissions).window(
                outsider,
                project_id=project.id,
                starts_on=MONTH_START,
                ends_on=MONTH_END,
            )


class TestSpanRule:
    """`_span_of` 와 SQL 의 겹침 판정은 **같은 규칙**이어야 한다.

    한쪽만 고치면 질의에는 걸리는데 놓을 자리가 없는 이슈가 생기고, 그건
    화면에서 조용히 사라진다.
    """

    def test_no_dates_cannot_be_placed(self) -> None:
        assert span_of(None, None) is None

    def test_only_a_due_date_is_one_day(self) -> None:
        assert span_of(None, date(2026, 9, 4)) == (date(2026, 9, 4), date(2026, 9, 4))

    def test_only_a_start_is_one_day(self) -> None:
        assert span_of(date(2026, 9, 4), None) == (date(2026, 9, 4), date(2026, 9, 4))

    def test_upside_down_is_flipped(self) -> None:
        assert span_of(date(2026, 9, 9), date(2026, 9, 2)) == (date(2026, 9, 2), date(2026, 9, 9))


def _all_issues(project: Project) -> object:
    from sqlalchemy import select

    return select(Issue).where(Issue.project_id == project.id)
