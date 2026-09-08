"""반복 이슈의 저장 경로와 실행 (feature-map A27). 실제 Postgres 를 쓴다.

스케줄 계산은 `test_issue_recurrence.py` 가 값으로 붙잡는다. 여기서 보는 것은
**그 계산이 저장과 실행에 제대로 이어졌는가**이고, 틀리면 조용히 틀린다:

- **밀린 만큼 몰아 만들지 않는다.** 앱이 일주일 내려갔다 올라왔을 때 7건이
  만들어지면 그건 복구가 아니라 알림 폭탄이다.
- **두 번 만들지 않는다.** 한 번 돌고 나면 다음 시각이 앞으로 가 있어야
  같은 주기에 두 번 집히지 않는다.
- **켜자마자 만들지 않는다.** 시험용으로 켠 스케줄이 즉시 실제 이슈를 내면
  사람은 그것을 지우고 기능을 안 쓴다.
- **만든 사람이 사라지면 끄고 이유를 남긴다.** 없는 사람 이름으로 계속
  만드는 것도, 조용히 멈추는 것도 옳지 않다.
- **꺼진 것은 안 돈다.**
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import Issue, IssueType, RecurringIssue, Workflow, WorkflowState
from ieum.modules.issues.recurrence import Schedule
from ieum.modules.issues.recurring import (
    STOPPED_OWNER_INACTIVE,
    NewRecurrence,
    RecurringIssueService,
    run_due,
)
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

SEOUL = "Asia/Seoul"


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    return service


@pytest_asyncio.fixture
async def issue_type(session: AsyncSession) -> AsyncIterator[IssueType]:
    wf = Workflow(name=f"WF-{secrets.token_hex(4)}", is_builtin=False)
    session.add(wf)
    await session.flush()
    for position, (name, category, is_initial) in enumerate(DEFAULT_WORKFLOW_STATES):
        session.add(
            WorkflowState(
                workflow_id=wf.id,
                name=name,
                category=category,
                position=position,
                is_initial=is_initial,
            )
        )
    await session.flush()
    row = IssueType(project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=wf.id)
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def project(session: AsyncSession) -> AsyncIterator[Project]:
    row = Project(key=f"R{secrets.token_hex(3).upper()}", name="Recurring Project")
    session.add(row)
    await session.flush()
    yield row


async def _member(session: AsyncSession, project: Project) -> tuple[User, Actor]:
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
    return user, Actor(user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow())


@pytest_asyncio.fixture
async def actor(session: AsyncSession, project: Project) -> Actor:
    _, found = await _member(session, project)
    return found


def _payload(project: Project, issue_type: IssueType, **over: object) -> NewRecurrence:
    base = {
        "project_id": project.id,
        "name": f"점검-{secrets.token_hex(3)}",
        "summary": "주간 점검",
        "schedule": Schedule(cadence="daily", hour=9, minute=0, timezone=SEOUL),
        "type_id": issue_type.id,
    }
    base.update(over)
    return NewRecurrence(**base)  # type: ignore[arg-type]


async def _count_issues(session: AsyncSession, project: Project) -> int:
    found = await session.scalar(
        select(func.count()).select_from(Issue).where(Issue.project_id == project.id)
    )
    return int(found or 0)


class TestCreating:
    async def test_the_first_run_is_in_the_future(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**켜자마자 만들지 않는다.**

        시험용으로 켠 스케줄이 즉시 실제 이슈를 내면 사람은 그걸 지우고 기능을
        안 쓴다. 다음 시각은 언제나 지금 뒤다.
        """
        row = await RecurringIssueService(session, permissions).create(
            actor, _payload(project, issue_type)
        )
        assert row.next_run_at > utcnow()
        assert row.is_enabled is True

    async def test_the_same_name_twice_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        service = RecurringIssueService(session, permissions)
        payload = _payload(project, issue_type, name="같은 이름")
        await service.create(actor, payload)
        with pytest.raises(ConflictError) as exc:
            await service.create(actor, payload)
        assert exc.value.code == "issues.recurrence_name_taken"

    async def test_a_schedule_that_cannot_run_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """저장 시점에 거절해야 쓴 사람이 고친다."""
        with pytest.raises(ValidationError) as exc:
            await RecurringIssueService(session, permissions).create(
                actor,
                _payload(
                    project,
                    issue_type,
                    schedule=Schedule(cadence="weekly", hour=9, minute=0, timezone=SEOUL),
                ),
            )
        assert exc.value.code == "issues.recurrence_needs_weekday"

    async def test_a_stranger_cannot_schedule_issues(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """스케줄은 **이슈를 만드는 일**이다. 만들 수 없는 사람이 예약할 수
        있으면 권한 검사를 우회하는 길이 된다."""
        stranger = Actor(user_id=uuid4(), email="x@example.com", is_active=True)
        with pytest.raises(PermissionDeniedError):
            await RecurringIssueService(session, permissions).create(
                stranger, _payload(project, issue_type)
            )

    async def test_changing_the_cadence_moves_the_next_run(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """주기를 고쳤는데 다음 한 번이 옛 시각에 돌면 사람은 안 고쳐진 줄 안다."""
        service = RecurringIssueService(session, permissions)
        row = await service.create(actor, _payload(project, issue_type))
        before = row.next_run_at
        found = await service.update(
            actor,
            row.id,
            payload=_payload(
                project,
                issue_type,
                name=row.name,
                schedule=Schedule(cadence="monthly", hour=9, minute=0, timezone=SEOUL, day=1),
            ),
        )
        assert found.next_run_at != before
        assert found.cadence == "monthly"


class TestRunning:
    async def test_it_creates_the_issue_and_moves_on(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        row = await RecurringIssueService(session, permissions).create(
            actor, _payload(project, issue_type, summary="주간 점검")
        )
        row.next_run_at = utcnow() - timedelta(minutes=1)
        await session.flush()

        assert await run_due(session, permissions) == 1

        assert await _count_issues(session, project) == 1
        # **다음 시각이 앞으로 가 있다.** 안 가면 같은 주기에 또 집힌다.
        assert row.next_run_at > utcnow()
        assert row.last_issue_id is not None
        assert row.last_run_at is not None

    async def test_running_twice_makes_one_issue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """전진이 만드는 것과 같은 트랜잭션이라 두 번째 훑기에는 안 걸린다."""
        row = await RecurringIssueService(session, permissions).create(
            actor, _payload(project, issue_type)
        )
        row.next_run_at = utcnow() - timedelta(minutes=1)
        await session.flush()

        assert await run_due(session, permissions) == 1
        assert await run_due(session, permissions) == 0
        assert await _count_issues(session, project) == 1

    async def test_a_week_of_downtime_makes_one_issue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이 시험이 이 파일의 이유다.**

        매일 도는 스케줄이 일주일 밀렸다. 놓친 시각 기준으로 다음을 잡으면
        따라잡기가 시작되어 같은 이슈가 7건 만들어진다 — 받는 사람은 7건을
        지운 뒤 스케줄을 끈다.
        """
        row = await RecurringIssueService(session, permissions).create(
            actor, _payload(project, issue_type)
        )
        row.next_run_at = utcnow() - timedelta(days=7)
        await session.flush()

        assert await run_due(session, permissions) == 1
        assert await run_due(session, permissions) == 0
        assert await _count_issues(session, project) == 1
        assert row.next_run_at > utcnow()

    async def test_a_disabled_schedule_does_not_run(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        row = await RecurringIssueService(session, permissions).create(
            actor, _payload(project, issue_type)
        )
        row.next_run_at = utcnow() - timedelta(minutes=1)
        row.is_enabled = False
        await session.flush()

        assert await run_due(session, permissions) == 0
        assert await _count_issues(session, project) == 0

    async def test_the_template_reaches_the_issue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """틀에 적은 것이 만들어진 이슈에 있어야 한다 — 기한은 **며칠 뒤**로."""
        row = await RecurringIssueService(session, permissions).create(
            actor,
            _payload(
                project,
                issue_type,
                summary="정산 마감",
                description="지난달 정산을 마감한다.",
                priority=2,
                assignee_id=actor.user_id,
                labels=("정산", "월간"),
                due_in_days=3,
            ),
        )
        row.next_run_at = utcnow() - timedelta(minutes=1)
        await session.flush()
        await run_due(session, permissions)

        issue = (
            await session.execute(select(Issue).where(Issue.project_id == project.id))
        ).scalar_one()
        assert issue.summary == "정산 마감"
        assert issue.description == "지난달 정산을 마감한다."
        assert issue.priority == 2
        assert issue.assignee_id == actor.user_id
        assert issue.reporter_id == actor.user_id
        assert issue.due_date == (utcnow() + timedelta(days=3)).date()

    async def test_an_inactive_owner_stops_it_with_a_reason(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**끄고 이유를 남긴다.**

        정지된 계정 이름으로 이슈가 계속 만들어지는 것도, 조용히 멈추는 것도
        옳지 않다. 사람이 목록에서 "왜 안 도는지" 를 읽을 수 있어야 한다.
        """
        user, owner = await _member(session, project)
        row = await RecurringIssueService(session, permissions).create(
            owner, _payload(project, issue_type)
        )
        row.next_run_at = utcnow() - timedelta(minutes=1)
        user.status = "suspended"
        await session.flush()

        assert await run_due(session, permissions) == 0
        assert row.is_enabled is False
        assert row.last_error == STOPPED_OWNER_INACTIVE
        assert await _count_issues(session, project) == 0

    async def test_one_broken_schedule_does_not_block_the_others(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**이 시험이 A27 에서 제일 비싼 버그를 잡는다.**

        접힌 프로젝트의 스케줄은 이슈를 못 만든다 — `create_authorized` 가
        거절한다. 그 거절을 그냥 올리면 트랜잭션이 통째로 되돌아가고, **접힌
        프로젝트 하나가 설치 전체의 반복을 영원히 멈춘다.** 15초마다 같은
        예외가 나고, 다른 스케줄은 한 건도 안 돌고, 그 사실은 워커 로그에만
        남는다 — 아무도 안 보는 곳이다.

        고장 난 줄을 **먼저** 두는 것이 요점이다(`next_run_at` 이 더 이르다).
        뒤에 두면 성한 줄이 이미 만들어진 뒤라 이 시험이 아무것도 증명하지
        못한다.
        """
        broken = Project(key=f"B{secrets.token_hex(3).upper()}", name="Archived Project")
        session.add(broken)
        await session.flush()
        _, owner = await _member(session, broken)
        service = RecurringIssueService(session, permissions)
        dead = await service.create(owner, _payload(broken, issue_type))
        alive = await service.create(actor, _payload(project, issue_type))
        dead.next_run_at = utcnow() - timedelta(minutes=5)
        alive.next_run_at = utcnow() - timedelta(minutes=1)
        broken.archived_at = utcnow()
        await session.flush()

        assert await run_due(session, permissions) == 1

        # 성한 줄은 돌았다.
        assert await _count_issues(session, project) == 1
        assert alive.next_run_at > utcnow()
        assert alive.is_enabled is True
        assert alive.last_error is None
        # 고장 난 줄만 꺼졌고, 이유가 코드로 남았다.
        assert dead.is_enabled is False
        assert dead.last_error == "issues.project_archived"
        assert await _count_issues(session, broken) == 0

    async def test_a_schedule_that_can_no_longer_be_computed_stops_only_itself(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """**저장된 스케줄이 계산 불가가 되는 날이 있다.**

        시간대 이름은 만들 때 검사하지만 행에 남는다. tzdata 가 그 이름을
        버리거나 누가 DB 를 직접 고치면 `next_after` 안의 `ZoneInfo` 가
        `IeumError` 가 **아닌** 예외를 낸다 — 그러면 줄마다 두른 `except` 를
        지나쳐 배치 전체가 되돌아가고, 15초마다 같은 예외가 나면서 설치 전체의
        반복이 멈춘다.

        그래서 돌기 전에 스케줄을 다시 검사한다. 못 도는 줄만 꺼지고 이유가
        코드로 남아 화면이 번역해 보여 준다.
        """
        service = RecurringIssueService(session, permissions)
        broken = await service.create(actor, _payload(project, issue_type))
        alive = await service.create(actor, _payload(project, issue_type))
        # 만들 때는 통과한 값을 나중에 못 쓰게 만든다 — 실제로 일어나는 방식이다.
        broken.timezone = "Mars/Olympus"
        broken.next_run_at = utcnow() - timedelta(minutes=5)
        alive.next_run_at = utcnow() - timedelta(minutes=1)
        await session.flush()

        assert await run_due(session, permissions) == 1

        assert broken.is_enabled is False
        assert broken.last_error == "issues.recurrence_unknown_timezone"
        assert alive.is_enabled is True
        assert alive.next_run_at > utcnow()
        assert await _count_issues(session, project) == 1

    async def test_turning_it_back_on_clears_the_reason(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """도는 스케줄에 "정지됨" 이유가 계속 붙어 있으면 화면이 거짓말을 한다."""
        service = RecurringIssueService(session, permissions)
        row = await service.create(actor, _payload(project, issue_type))
        row.is_enabled = False
        row.last_error = STOPPED_OWNER_INACTIVE
        await session.flush()

        found = await service.update(actor, row.id, is_enabled=True)
        assert found.is_enabled is True
        assert found.last_error is None
        assert found.next_run_at > utcnow()


class TestDeleting:
    async def test_deleting_the_schedule_keeps_the_issues(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """만들어진 이슈는 스케줄의 사본이 아니다. 스케줄을 지운다고 사라지면
        지난 점검 기록이 통째로 사라진다."""
        service = RecurringIssueService(session, permissions)
        row = await service.create(actor, _payload(project, issue_type))
        row.next_run_at = utcnow() - timedelta(minutes=1)
        await session.flush()
        await run_due(session, permissions)

        await service.delete(actor, row.id)
        await session.flush()

        assert await _count_issues(session, project) == 1
        left = await session.scalar(select(func.count()).select_from(RecurringIssue))
        assert left == 0
