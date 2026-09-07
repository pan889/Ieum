"""SLA 클럭이 실제로 움직이는지 (feature-map C4·C5).

`calendar.py`·`sla.py` 는 순수 함수라 손으로 계산해 시험했다. 여기는 **행을
쓰는 층**이다: 클럭이 걸리고, 멈추고, 끝나고, 위반이 잡히는지 실제 DB 로 본다.

붙잡는 것:

- `started_at` 은 **티켓이 만들어진 시각**이다. 지금으로 두면 아웃박스가 늦게
  훑은 만큼 모든 티켓이 공짜 시간을 얻는다.
- **내부 노트는 첫 응답이 아니다.** 고객이 볼 수 없는 글로 SLA 를 지킬 수
  있게 두면 그 지표는 아무 것도 뜻하지 않는다.
- **고객 자신의 회신도 응답이 아니다.** 고객이 한 번 더 물어서 우리 SLA 가
  지켜지는 것은 거꾸로다.
- **같은 이벤트를 두 번 받아도 목표가 다시 계산되지 않는다.**
- **멈춰 있는 클럭은 위반으로 잡히지 않는다.** 고객 답변을 기다리는 티켓이
  저절로 위반되면 그건 아무도 잘못하지 않은 위반이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, set_permission_service
from ieum.modules.desk import clock as desk_clock
from ieum.modules.desk.models import BusinessCalendarRow, SlaClock, SlaPolicy, TicketExt
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver

HOUR = 3600

#: 항상 열려 있는 달력. 클럭의 **배선**을 보는 시험이므로 업무 시간 계산을
#: 끼워 넣지 않는다 — 그건 `test_desk_calendar.py` 가 이미 26개로 본다.
ALWAYS_OPEN = {str(day): [["00:00", "23:59"]] for day in range(7)}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _calendar(
    session: AsyncSession, hours: dict[str, object] | None = None
) -> BusinessCalendarRow:
    row = BusinessCalendarRow(
        name=f"cal-{new_id()}",
        timezone="Asia/Seoul",
        working_hours=hours if hours is not None else ALWAYS_OPEN,
        holidays=[],
    )
    session.add(row)
    await session.flush()
    return row


async def _ticket(
    session: AsyncSession, *, created_at: datetime | None = None, priority: int = 3
) -> tuple[Project, Issue, User]:
    """프로젝트·이슈·티켓 확장 한 벌. 포털을 거치지 않는다 — 이 시험이 보는
    것은 클럭이고, 포털 흐름은 다른 파일이 본다."""
    project = Project(key=f"S{new_id().hex[-6:].upper()}", name="SLA")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    open_state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(open_state)
    issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    customer = User(
        email=f"c-{new_id()}@example.com", display_name="고객", status="active", is_customer=True
    )
    session.add(customer)
    await session.flush()

    issue = Issue(
        project_id=project.id,
        type_id=issue_type.id,
        state_id=open_state.id,
        key_seq=1,
        summary="프린터가 안 됩니다",
        priority=priority,
    )
    session.add(issue)
    await session.flush()
    if created_at is not None:
        issue.created_at = created_at
        await session.flush()
    session.add(TicketExt(issue_id=issue.id, reporter_customer_id=customer.id, channel="portal"))
    await session.flush()
    return project, issue, customer


async def _policy(
    session: AsyncSession,
    project: Project,
    *,
    metric: str = "first_response",
    seconds: int = 4 * HOUR,
    calendar: BusinessCalendarRow | None = None,
    pause_state_ids: list[object] | None = None,
) -> SlaPolicy:
    row = SlaPolicy(
        project_id=project.id,
        name=f"policy-{new_id()}",
        metric=metric,
        calendar_id=(calendar or await _calendar(session)).id,
        goals=[{"seconds": seconds}],
        pause_state_ids=pause_state_ids or [],
    )
    session.add(row)
    await session.flush()
    return row


async def _clock(session: AsyncSession, issue: Issue, policy: SlaPolicy) -> SlaClock | None:
    return await session.get(SlaClock, (issue.id, policy.id))


class TestStartingTheClock:
    async def test_it_starts_from_when_the_ticket_was_made(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**지금이 아니라 티켓이 만들어진 시각이다.**

        지금으로 두면 아웃박스가 5분 밀린 날에는 모든 티켓이 5분씩 유리해진다.
        """
        made = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
        project, issue, _ = await _ticket(session, created_at=made)
        policy = await _policy(session, project, seconds=2 * HOUR)

        assert await desk_clock.start_clocks(session, issue.id) == 1
        row = await _clock(session, issue, policy)
        assert row is not None
        assert row.started_at.astimezone(UTC) == made
        # 항상 열린 달력이라 목표는 정확히 2시간 뒤다.
        assert row.target_at.astimezone(UTC) == made + timedelta(hours=2)

    async def test_a_second_event_does_not_recompute_the_target(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """아웃박스가 같은 이벤트를 두 번 줄 수 있다. 목표가 다시 계산되면
        재시도한 날의 티켓만 목표가 달라진다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        first = await _clock(session, issue, policy)
        assert first is not None
        target = first.target_at

        assert await desk_clock.start_clocks(session, issue.id) == 0
        again = await _clock(session, issue, policy)
        assert again is not None
        assert again.target_at == target

    async def test_a_disabled_policy_does_not_get_a_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        policy.is_enabled = False
        await session.flush()
        assert await desk_clock.start_clocks(session, issue.id) == 0

    async def test_a_plain_issue_gets_no_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓이 아닌 이슈에는 SLA 가 없다 — 고객에게 한 약속이 아니다."""
        project, issue, _ = await _ticket(session)
        await _policy(session, project)
        await session.delete(await session.get(TicketExt, issue.id))
        await session.flush()
        assert await desk_clock.start_clocks(session, issue.id) == 0

    async def test_a_broken_calendar_does_not_kill_the_worker(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**달력 하나가 망가졌다고 다른 프로젝트의 클럭까지 멈추면 피해가
        번진다.** 저장할 때 검증하므로 여기 오는 것은 손으로 고친 행이다."""
        project, issue, _ = await _ticket(session)
        broken = await _calendar(session, hours={"0": "월요일 아침부터"})
        await _policy(session, project, calendar=broken)
        # 예외가 아니라 0 이다.
        assert await desk_clock.start_clocks(session, issue.id) == 0


class TestFinishingTheFirstResponse:
    async def test_a_public_reply_from_an_agent_completes_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 1
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is not None

    async def test_an_internal_note_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**고객이 볼 수 없는 글로 SLA 를 지킬 수 있게 두면 그 지표는 아무
        것도 뜻하지 않는다.**"""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=True) == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None

    async def test_the_customers_own_reply_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객이 한 번 더 물어서 우리 SLA 가 지켜지는 것은 거꾸로다."""
        project, issue, customer = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=customer.id, is_internal=False)
            == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None

    async def test_a_second_reply_does_not_move_the_first_response(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """갱신되면 그 지표는 "마지막 응답" 이 된다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)
        agent = await _person(session)
        await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
        row = await _clock(session, issue, policy)
        assert row is not None
        first = row.completed_at

        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 0
        )
        assert row.completed_at == first

    async def test_a_reply_does_not_complete_the_resolution_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """회신은 해결이 아니다. 두 지표를 한 이벤트로 끝내면 해결 SLA 가
        의미를 잃는다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None


class TestFinishingTheResolution:
    async def test_moving_to_done_completes_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        assert await desk_clock.on_transition(session, issue.id, to_state_category="done") >= 1
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is not None

    async def test_moving_within_open_states_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        await desk_clock.on_transition(session, issue.id, to_state_category="in_progress")
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None


class TestSweepingForBreaches:
    async def test_it_finds_a_clock_past_its_target(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        found = await desk_clock.sweep_breaches(session)
        assert [c.issue_id for c in found] == [issue.id]
        assert row.breached_at is not None

    async def test_it_does_not_report_the_same_breach_twice(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`breached_at` 은 "위반인가" 가 아니라 "알렸는가" 다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        assert len(await desk_clock.sweep_breaches(session)) == 1
        assert await desk_clock.sweep_breaches(session) == []

    async def test_a_paused_clock_is_not_a_breach(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 클래스의 이유다.** 고객 답변을 기다리는 티켓이
        저절로 위반되면 그건 아무도 잘못하지 않은 위반이다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        row.paused_at = datetime.now(UTC) - timedelta(hours=2)
        await session.flush()

        assert await desk_clock.sweep_breaches(session) == []

    async def test_a_completed_clock_is_not_a_breach(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이미 지킨 약속이 나중에 위반으로 잡히면 지표가 뒤집힌다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(hours=2)
        row.completed_at = datetime.now(UTC) - timedelta(hours=3)
        await session.flush()

        assert await desk_clock.sweep_breaches(session) == []


class TestPausing:
    async def test_it_pauses_in_a_named_state_and_resumes_out_of_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """일시정지는 **상태 id** 로 정한다 — 이름도 category 도 아니다."""
        project, issue, _ = await _ticket(session)
        state_id = issue.state_id
        policy = await _policy(session, project, pause_state_ids=[state_id])
        await desk_clock.start_clocks(session, issue.id)

        # 이 상태로 들어오면 멈춘다.
        await desk_clock.on_transition(session, issue.id, to_state_category="todo")
        row = await _clock(session, issue, policy)
        assert row is not None and row.paused_at is not None

        # 다른 상태로 나가면 다시 돈다. 상태를 실제로 바꿔 준다.
        other = WorkflowState(
            workflow_id=(await session.get(Workflow, (await _workflow_id(session, issue)))).id,
            name="In Progress",
            category="in_progress",
            position=1,
            is_initial=False,
        )
        session.add(other)
        await session.flush()
        issue.state_id = other.id
        await session.flush()

        await desk_clock.on_transition(session, issue.id, to_state_category="in_progress")
        assert row.paused_at is None
        # 기다린 만큼이 기록된다.
        assert row.paused_seconds >= 0


async def _person(session: AsyncSession) -> User:
    row = User(email=f"a-{new_id()}@example.com", display_name="상담원", status="active")
    session.add(row)
    await session.flush()
    return row


async def _workflow_id(session: AsyncSession, issue: Issue) -> object:
    issue_type = await session.get(IssueType, issue.type_id)
    assert issue_type is not None
    return issue_type.workflow_id


async def _count_clocks(session: AsyncSession) -> int:
    return len(list((await session.execute(select(SlaClock))).scalars().all()))
