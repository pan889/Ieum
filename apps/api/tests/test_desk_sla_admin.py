"""SLA 정책·업무 달력 관리 (feature-map C4).

`sla.py` 가 규칙을 갖고 있고 여기는 **저장 경로**다: 성립하지 않는 것이
저장되지 않는지, 권한이 맞는지, 그리고 고칠 때 지난 판정이 흔들리지 않는지.

붙잡는 것:

- **성립하지 않는 달력·정책은 저장 전에 거절한다.** 망가진 SLA 는 저장되고,
  클럭이 안 걸리고, 화면의 SLA 칸이 비어 있을 뿐이다 — 아무도 그것이 빠졌다는
  것을 모른다.
- **`metric` 은 바꿀 수 없다.** 응답 정책을 해결 정책으로 바꾸면 이미 걸린
  클럭들이 갑자기 다른 것을 재는 시계가 된다.
- **쓰는 정책이 있는 달력은 못 지운다.** FK 가 `RESTRICT` 라 그냥 두면 DB
  오류로 500 이 되고, 관리자는 무엇이 막았는지 못 듣는다.
- **정책을 지워도 이미 걸린 클럭은 그대로다.** 지난 티켓의 판정이 정책을
  지우는 것으로 바뀌면 안 된다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StepUpRequiredError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.models import SlaClock, SlaPolicy
from ieum.modules.desk.service import SlaAdminService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

HOUR = 3600
WEEK = {str(day): [["09:00", "18:00"]] for day in range(5)}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _project(session: AsyncSession) -> Project:
    row = Project(key=f"A{new_id().hex[-6:].upper()}", name="SLA admin")
    session.add(row)
    await session.flush()
    return row


async def _admin(session: AsyncSession) -> User:
    """`desk.sla.manage` 를 가진 사람. 전역 권한이다."""
    row = User(email=f"sa-{new_id()}@example.com", display_name="관리자", status="active")
    session.add(row)
    await session.flush()
    await grant(
        session,
        principal_id=row.id,
        permissions_granted=(desk_perms.SLA_MANAGE,),
        scope=Scope.global_(),
    )
    return row


async def _project_with_state(session: AsyncSession) -> tuple[Project, WorkflowState]:
    """프로젝트와 **그 프로젝트에서 실제로 쓸 수 있는** 상태 하나.

    유형을 프로젝트 전용으로 만드는 이유: 전역 유형으로 만들면 모든 프로젝트가
    그 상태를 고를 수 있게 되어, "다른 프로젝트의 상태는 거절한다" 를 보는
    시험이 아무것도 안 보게 된다.
    """
    project = await _project(session)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(state)
    session.add(IssueType(project_id=project.id, name="Request", workflow_id=workflow.id))
    await session.flush()
    return project, state


async def _calendar_id(
    session: AsyncSession, permissions: PermissionService, admin: User
) -> object:
    view = await SlaAdminService(session, permissions).create_calendar(
        actor_for(admin),
        name=f"Seoul {new_id().hex[-4:]}",
        timezone="Asia/Seoul",
        working_hours=WEEK,
        holidays=[],
    )
    return view.calendar.id


async def _nobody(session: AsyncSession) -> User:
    row = User(email=f"nb-{new_id()}@example.com", display_name="아무나", status="active")
    session.add(row)
    await session.flush()
    return row


class TestSavingACalendar:
    async def test_a_good_calendar_saves(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        view = await SlaAdminService(session, permissions).create_calendar(
            actor_for(admin),
            name=f"Seoul {new_id().hex[-4:]}",
            timezone="Asia/Seoul",
            working_hours=WEEK,
            holidays=["2026-09-08"],
        )
        assert view.calendar.timezone == "Asia/Seoul"
        assert view.calendar.holidays == ["2026-09-08"]

    @pytest.mark.parametrize(
        "hours",
        [
            {},  # 업무 시간이 하루도 없다 — 클럭이 영원히 멈춘다
            {"0": [["09:00", "13:00"], ["12:00", "18:00"]]},  # 겹친다 — 두 번 세어진다
            {"0": [["18:00", "09:00"]]},  # 거꾸로다
            {"7": [["09:00", "18:00"]]},  # 요일이 아니다
        ],
    )
    async def test_a_calendar_that_cannot_measure_is_refused(
        self, session: AsyncSession, permissions: PermissionService, hours: dict[str, object]
    ) -> None:
        """**저장 전에 계산기에 넣어 본다.** 통과하면 SLA 가 조용히 이상한
        숫자를 내고, 그건 며칠 뒤에 "SLA 가 안 맞는다" 로만 보인다."""
        admin = await _admin(session)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_calendar(
                actor_for(admin),
                name=f"bad {new_id().hex[-4:]}",
                timezone="Asia/Seoul",
                working_hours=hours,
                holidays=[],
            )
        assert exc.value.code == "desk.calendar_invalid"

    async def test_an_unknown_timezone_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        with pytest.raises(ValidationError):
            await SlaAdminService(session, permissions).create_calendar(
                actor_for(admin),
                name=f"mars {new_id().hex[-4:]}",
                timezone="Mars/Olympus",
                working_hours=WEEK,
                holidays=[],
            )

    async def test_editing_into_a_broken_shape_is_refused_too(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """만들 때만 보면, 고칠 때 망가진 달력이 들어온다."""
        admin = await _admin(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_calendar(
            actor_for(admin),
            name=f"Seoul {new_id().hex[-4:]}",
            timezone="Asia/Seoul",
            working_hours=WEEK,
            holidays=[],
        )
        with pytest.raises(ValidationError):
            await service.update_calendar(actor_for(admin), view.calendar.id, working_hours={})

    async def test_a_duplicate_name_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        service = SlaAdminService(session, permissions)
        name = f"Seoul {new_id().hex[-4:]}"
        await service.create_calendar(
            actor_for(admin),
            name=name,
            timezone="Asia/Seoul",
            working_hours=WEEK,
            holidays=[],
        )
        with pytest.raises(ConflictError):
            await service.create_calendar(
                actor_for(admin),
                name=name,
                timezone="Asia/Seoul",
                working_hours=WEEK,
                holidays=[],
            )


class TestArchivingFreesTheName:
    """편도로 보관되는 것들이 이름을 한 번 쓰고 버리고 있었다.

    보관하는 판단은 맞다 — 지난 티켓의 판정이 정책을 지우는 것으로 바뀌면
    안 된다. 틀린 것은 **유일성의 범위**였다. "같은 이름의 정책이 둘이면
    안 된다" 가 말하는 정책은 살아 있는 정책이다.
    """

    async def test_a_calendar_name_comes_back_after_archiving(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        service = SlaAdminService(session, permissions)
        name = f"Seoul {new_id().hex[-4:]}"
        first = await service.create_calendar(
            actor_for(admin),
            name=name,
            timezone="Asia/Seoul",
            working_hours=WEEK,
            holidays=[],
        )
        await service.delete_calendar(actor_for(admin), first.calendar.id)

        again = await service.create_calendar(
            actor_for(admin),
            name=name,
            timezone="Asia/Seoul",
            working_hours=WEEK,
            holidays=[],
        )
        assert again.calendar.id != first.calendar.id

    async def test_a_policy_name_comes_back_after_archiving(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        goals = [{"seconds": HOUR}]

        first = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=goals,
            pause_state_ids=[],
        )
        await service.delete_policy(actor_for(admin), first.policy.id)

        again = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=goals,
            pause_state_ids=[],
        )
        assert again.policy.id != first.policy.id

    async def test_two_live_policies_still_cannot_share_a_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        goals = [{"seconds": HOUR}]
        await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=goals,
            pause_state_ids=[],
        )
        with pytest.raises(ConflictError):
            await service.create_policy(
                actor_for(admin),
                project_id=project.id,
                name="첫 응답",
                metric="resolution",
                calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
                goals=goals,
                pause_state_ids=[],
            )


class TestSavingAPolicy:
    async def test_a_good_policy_saves_with_its_calendar_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """목록이 "무엇으로 재는지" 를 바로 보여 줘야 한다 — id 만 주면 화면이
        달력 목록을 또 받아 짜맞춰야 한다."""
        admin = await _admin(session)
        project = await _project(session)
        calendar_id = await _calendar_id(session, permissions, admin)
        view = await SlaAdminService(session, permissions).create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=calendar_id,  # type: ignore[arg-type]
            goals=[{"priority_min": 5, "seconds": HOUR}, {"seconds": 4 * HOUR}],
            pause_state_ids=[],
        )
        assert view.policy.metric == "first_response"
        assert view.calendar_name.startswith("Seoul")

    async def test_a_policy_with_no_default_goal_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """조건에 안 걸리는 티켓은 클럭 없이 굴러가고, 화면의 SLA 칸이 비어
        있을 뿐이라 아무도 모른다."""
        admin = await _admin(session)
        project = await _project(session)
        calendar_id = await _calendar_id(session, permissions, admin)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="반쪽",
                metric="first_response",
                calendar_id=calendar_id,  # type: ignore[arg-type]
                goals=[{"priority_min": 4, "seconds": HOUR}],
                pause_state_ids=[],
            )
        assert exc.value.code == "desk.sla_goals_invalid"

    async def test_a_typo_in_a_condition_key_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """오타 난 키를 조용히 무시하면 그 항목은 조건이 없는 셈이 되어
        **언제나 맞는** 목표가 된다 — 관리자는 "우선순위 4 이상만 1시간" 이라고
        적었는데 모든 티켓이 1시간이 된다. 그리고 **어느 키가 틀렸는지**
        문구에 담긴다.
        """
        admin = await _admin(session)
        project = await _project(session)
        calendar_id = await _calendar_id(session, permissions, admin)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="오타",
                metric="first_response",
                calendar_id=calendar_id,  # type: ignore[arg-type]
                goals=[{"prioirty_min": 4, "seconds": HOUR}],
                pause_state_ids=[],
            )
        assert "prioirty_min" in exc.value.message

    async def test_an_unknown_metric_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        calendar_id = await _calendar_id(session, permissions, admin)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="모르는 지표",
                metric="csat",
                calendar_id=calendar_id,  # type: ignore[arg-type]
                goals=[{"seconds": HOUR}],
                pause_state_ids=[],
            )
        assert exc.value.code == "desk.sla_unknown_metric"

    async def test_a_missing_calendar_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """달력 없이 목표만 있으면 무엇으로 재는지 알 수 없다."""
        admin = await _admin(session)
        project = await _project(session)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="달력 없음",
                metric="first_response",
                calendar_id=new_id(),
                goals=[{"seconds": HOUR}],
                pause_state_ids=[],
            )
        assert exc.value.code == "desk.sla_calendar_missing"


class TestPauseStates:
    """멈춤 상태는 **고르는 것**이고, 고를 수 없는 것은 저장되지 않는다.

    검증 없이 받으면 잘못된 UUID 가 그대로 저장되고 시계는 영원히 안 멈춘다 —
    관리자는 멈춤을 설정했다고 믿고, 아무 일도 일어나지 않으며, 틀렸다는
    신호가 어디에도 없다. 이 결함이 조용한 이유는 멈춤이 **일어나지 않는
    일**이라서다: 화면에 붉은 줄이 뜨지 않는다.
    """

    async def test_it_offers_the_states_this_project_can_be_in(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """UUID 를 손으로 적게 하지 않으려면 고를 수 있는 것을 내줘야 한다."""
        admin = await _admin(session)
        project, state = await _project_with_state(session)
        rows = await SlaAdminService(session, permissions).list_states(actor_for(admin), project.id)
        found = next((r for r in rows if r.id == state.id), None)
        assert found is not None
        assert found.name == "Open"
        assert found.category == "todo"
        # 같은 이름의 상태가 워크플로우마다 따로 있다 — 어느 쪽인지 알아야 한다.
        assert found.workflow_name

    async def test_a_state_from_another_project_is_not_offered(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """다른 프로젝트 전용 유형의 상태를 걸어 두면 멈춤이 영원히 안 온다."""
        admin = await _admin(session)
        mine, _ = await _project_with_state(session)
        _, theirs = await _project_with_state(session)
        rows = await SlaAdminService(session, permissions).list_states(actor_for(admin), mine.id)
        assert theirs.id not in {r.id for r in rows}

    async def test_a_chosen_state_saves(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project, state = await _project_with_state(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[state.id],
        )
        assert view.policy.pause_state_ids == [state.id]

    async def test_an_unknown_state_is_refused_on_create(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="첫 응답",
                metric="first_response",
                calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
                goals=[{"seconds": 4 * HOUR}],
                pause_state_ids=[new_id()],
            )
        assert exc.value.code == "desk.sla_pause_state_unknown"

    async def test_a_state_from_another_project_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**있는 상태인지가 아니라 이 프로젝트의 상태인지를 본다.** 존재만
        보면 다른 프로젝트의 상태가 통과하고, 그 티켓은 그 상태에 갈 수 없으니
        멈춤은 영원히 오지 않는다."""
        admin = await _admin(session)
        mine = await _project(session)
        _, theirs = await _project_with_state(session)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=mine.id,
                name="첫 응답",
                metric="first_response",
                calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
                goals=[{"seconds": 4 * HOUR}],
                pause_state_ids=[theirs.id],
            )
        assert exc.value.code == "desk.sla_pause_state_unknown"

    async def test_an_unknown_state_is_refused_on_update(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고칠 때도 본다. 만들 때만 보면 고치기로 우회할 수 있다."""
        admin = await _admin(session)
        project, state = await _project_with_state(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[state.id],
        )
        with pytest.raises(ValidationError) as exc:
            await service.update_policy(
                actor_for(admin), view.policy.id, pause_state_ids=[new_id()]
            )
        assert exc.value.code == "desk.sla_pause_state_unknown"

    async def test_clearing_the_list_is_allowed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """빈 목록은 "멈추지 않는다" 는 뜻이고, 그것도 고를 수 있어야 한다."""
        admin = await _admin(session)
        project, state = await _project_with_state(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[state.id],
        )
        updated = await service.update_policy(actor_for(admin), view.policy.id, pause_state_ids=[])
        assert updated.policy.pause_state_ids == []

    async def test_listing_states_needs_the_permission(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _nobody(session)
        project = await _project(session)
        with pytest.raises(PermissionDeniedError):
            await SlaAdminService(session, permissions).list_states(actor_for(nobody), project.id)


class TestSavingEscalations:
    """실행되지 않는 규칙은 **조용하다.** 밤에 아무도 호출되지 않았다는 사실은
    아침에야, 그것도 운이 좋으면 드러난다 — 그래서 저장할 때 거절한다.

    규칙의 계산은 `test_desk_sla.py` 가 순수 함수로 본다. 여기는 **저장
    경로**가 그 검증을 실제로 부르는지, 그리고 에러 코드가 맞는지를 본다.
    """

    async def test_rules_save_and_come_back(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        rules = [
            {"at_percent": 75, "action": "notify", "user_id": str(admin.id)},
            {"at_percent": 100, "action": "raise_priority", "priority": 5},
        ]
        view = await SlaAdminService(session, permissions).create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[],
            escalations=rules,
        )
        assert view.policy.escalations == rules

    async def test_no_rules_is_the_normal_case(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """에스컬레이션 없는 정책이 대부분이다. 목표와 달리 기본 규칙을
        요구하지 않는다."""
        admin = await _admin(session)
        project = await _project(session)
        view = await SlaAdminService(session, permissions).create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[],
        )
        assert view.policy.escalations == []

    async def test_a_rule_that_cannot_run_is_refused_on_create(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        with pytest.raises(ValidationError) as exc:
            await SlaAdminService(session, permissions).create_policy(
                actor_for(admin),
                project_id=project.id,
                name="첫 응답",
                metric="first_response",
                calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
                goals=[{"seconds": 4 * HOUR}],
                pause_state_ids=[],
                escalations=[{"at_percent": 75, "action": "delete_everything"}],
            )
        assert exc.value.code == "desk.sla_escalation_invalid"

    async def test_a_rule_that_cannot_run_is_refused_on_update(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """만들 때만 보면 고치기로 우회할 수 있다."""
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[],
        )
        with pytest.raises(ValidationError) as exc:
            await service.update_policy(
                actor_for(admin),
                view.policy.id,
                escalations=[{"at_percent": 999, "action": "notify", "user_id": str(admin.id)}],
            )
        assert exc.value.code == "desk.sla_escalation_invalid"

    async def test_clearing_the_rules_is_allowed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        view = await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="첫 응답",
            metric="first_response",
            calendar_id=await _calendar_id(session, permissions, admin),  # type: ignore[arg-type]
            goals=[{"seconds": 4 * HOUR}],
            pause_state_ids=[],
            escalations=[{"at_percent": 100, "action": "raise_priority", "priority": 5}],
        )
        updated = await service.update_policy(actor_for(admin), view.policy.id, escalations=[])
        assert updated.policy.escalations == []


class TestNotBreakingPastVerdicts:
    async def test_a_calendar_in_use_cannot_be_deleted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """FK 가 `RESTRICT` 라 그냥 두면 DB 오류로 500 이 되고, 관리자는
        무엇이 막았는지 못 듣는다."""
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        calendar = (
            await service.create_calendar(
                actor_for(admin),
                name=f"Seoul {new_id().hex[-4:]}",
                timezone="Asia/Seoul",
                working_hours=WEEK,
                holidays=[],
            )
        ).calendar
        await service.create_policy(
            actor_for(admin),
            project_id=project.id,
            name="응답",
            metric="first_response",
            calendar_id=calendar.id,
            goals=[{"seconds": HOUR}],
            pause_state_ids=[],
        )
        with pytest.raises(ConflictError) as exc:
            await service.delete_calendar(actor_for(admin), calendar.id)
        assert exc.value.code == "desk.calendar_in_use"

    async def test_deleting_a_policy_leaves_its_clocks(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**지난 티켓의 판정이 정책을 지우는 것으로 바뀌면 안 된다.**

        정책은 보관되고, 이미 걸린 클럭은 그대로 남는다.
        """
        from datetime import UTC, datetime

        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        calendar = (
            await service.create_calendar(
                actor_for(admin),
                name=f"Seoul {new_id().hex[-4:]}",
                timezone="Asia/Seoul",
                working_hours=WEEK,
                holidays=[],
            )
        ).calendar
        policy = (
            await service.create_policy(
                actor_for(admin),
                project_id=project.id,
                name="응답",
                metric="first_response",
                calendar_id=calendar.id,
                goals=[{"seconds": HOUR}],
                pause_state_ids=[],
            )
        ).policy

        # 클럭을 손으로 하나 심는다. 이 시험이 보는 것은 정책을 지웠을 때
        # 그 행이 남는가이므로, 티켓 한 벌을 만들 이유가 없다.
        from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState

        workflow = Workflow(name=f"wf-{new_id()}")
        session.add(workflow)
        await session.flush()
        state = WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
        session.add(state)
        issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
        session.add(issue_type)
        await session.flush()
        issue = Issue(
            project_id=project.id,
            type_id=issue_type.id,
            state_id=state.id,
            key_seq=1,
            summary="티켓",
        )
        session.add(issue)
        await session.flush()
        now = datetime.now(UTC)
        session.add(SlaClock(issue_id=issue.id, policy_id=policy.id, started_at=now, target_at=now))
        await session.flush()

        await service.delete_policy(actor_for(admin), policy.id)

        rows = list(
            (await session.execute(select(SlaClock).where(SlaClock.policy_id == policy.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1, "정책을 지웠더니 클럭이 사라졌다"
        # 정책 자체는 보관됐다 — 목록에서는 사라지고 클럭의 근거는 남는다.
        kept = await session.get(SlaPolicy, policy.id)
        assert kept is not None and kept.archived_at is not None

    async def test_a_deleted_policy_is_not_listed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _admin(session)
        project = await _project(session)
        service = SlaAdminService(session, permissions)
        calendar = (
            await service.create_calendar(
                actor_for(admin),
                name=f"Seoul {new_id().hex[-4:]}",
                timezone="Asia/Seoul",
                working_hours=WEEK,
                holidays=[],
            )
        ).calendar
        policy = (
            await service.create_policy(
                actor_for(admin),
                project_id=project.id,
                name="응답",
                metric="first_response",
                calendar_id=calendar.id,
                goals=[{"seconds": HOUR}],
                pause_state_ids=[],
            )
        ).policy
        await service.delete_policy(actor_for(admin), policy.id)
        assert await service.list_policies(actor_for(admin), project.id) == []
        with pytest.raises(NotFoundError):
            await service.delete_policy(actor_for(admin), policy.id)


class TestPermissions:
    async def test_a_stranger_cannot_read_calendars(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _nobody(session)
        with pytest.raises(PermissionDeniedError):
            await SlaAdminService(session, permissions).list_calendars(actor_for(nobody))

    async def test_a_stranger_cannot_define_a_policy(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _nobody(session)
        project = await _project(session)
        with pytest.raises(PermissionDeniedError):
            await SlaAdminService(session, permissions).create_policy(
                actor_for(nobody),
                project_id=project.id,
                name="몰래",
                metric="first_response",
                calendar_id=new_id(),
                goals=[{"seconds": HOUR}],
                pause_state_ids=[],
            )

    async def test_it_needs_step_up(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**SLA 는 조직이 고객에게 한 약속이고, 목표를 늘리면 지표가
        좋아진다.** 자기가 평가받는 숫자를 자기가 고치는 자리다.

        `actor_for` 는 언제나 MFA 를 통과한 액터를 준다(대부분의 시험이 그것을
        원한다). 여기서는 통과하지 **않은** 액터가 필요하므로 직접 만든다 —
        시각도 비우고 `mfa_verified` 도 False 다: 시각만 비우면 "등록만 하고
        통과하지 않은" 계정과 구별되지 않는다.
        """
        admin = await _admin(session)
        not_verified = Actor(
            user_id=admin.id, email=admin.email, is_active=True, mfa_verified=False
        )
        # **`PermissionDeniedError` 가 아니다.** 권한은 있고 증명이 없는
        # 것이므로 다른 예외다 — 화면이 "권한 없음" 이 아니라 2FA 를 물어야
        # 한다. 둘을 한 예외로 두면 화면은 그 둘을 구별할 수 없다.
        with pytest.raises(StepUpRequiredError):
            await SlaAdminService(session, permissions).list_calendars(not_verified)
