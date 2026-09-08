"""IQL 이력 연산자 WAS/CHANGED (M5). 실제 Postgres 를 쓴다.

붙잡는 것 — 앞의 둘이 이 파일의 이유다:

- **`WAS` 는 지금 값도 답에 넣는다.** 만들 때부터 그 상태였던 이슈에는
  그렇다고 적힌 이력 줄이 없다. 이력만 보면 그 이슈들이 통째로 빠지는데,
  그건 "그런 이슈가 없다" 와 구분되지 않는다.
- **`CHANGED FROM x TO y` 는 한 변경 안에서** x→y 여야 한다. x→z, z→y 로
  따로 일어난 두 변경을 이어 붙이면 없던 일을 있다고 답한다.
- 창을 적으면 "그 창 동안 갖고 있었나" 다. 창 안에 변경이 없어도, 창이
  시작될 때 그 값이었으면 참이다.
- 이력을 물을 수 없는 필드는 **거절한다.** 조용히 빈 결과를 주면 "이력이
  없다" 와 똑같이 생긴다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql import errors as iql_errors
from ieum.modules.issues.models import (
    Issue,
    IssueHistory,
    IssueType,
    Workflow,
    WorkflowState,
)
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    return service


@pytest_asyncio.fixture
async def world(
    session: AsyncSession, permissions: PermissionService
) -> AsyncIterator[dict[str, Any]]:
    workflow = Workflow(name=f"WF-{secrets.token_hex(4)}")
    session.add(workflow)
    await session.flush()
    states: dict[str, WorkflowState] = {}
    for position, (name, category, is_initial) in enumerate(DEFAULT_WORKFLOW_STATES):
        state = WorkflowState(
            workflow_id=workflow.id,
            name=name,
            category=category,
            position=position,
            is_initial=is_initial,
        )
        session.add(state)
        states[name] = state
    issue_type = IssueType(
        project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=workflow.id
    )
    session.add(issue_type)
    project = Project(key=f"H{secrets.token_hex(3).upper()}", name="History Test")
    session.add(project)
    user = User(email=f"u-{new_id()}@e.com", display_name="사람", status="active")
    other = User(email=f"o-{new_id()}@e.com", display_name="다른 사람", status="active")
    session.add_all([user, other])
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

    actor = Actor(user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow())
    yield {
        "project": project,
        "type": issue_type,
        "states": states,
        "actor": actor,
        "user": user,
        "other": other,
    }


async def make(
    session: AsyncSession,
    permissions: PermissionService,
    world: dict[str, Any],
    summary: str,
) -> UUID:
    view = await IssueService(session, permissions).create(
        world["actor"],
        NewIssue(project_id=world["project"].id, type_id=world["type"].id, summary=summary),
    )
    return view.issue.id


async def record(
    session: AsyncSession,
    issue_id: UUID,
    changes: list[dict[str, Any]],
    *,
    when: datetime | None = None,
) -> None:
    """이력 한 줄을 직접 넣는다.

    서비스로 전이를 돌리면 시각을 고를 수 없다. 창 조건은 **시각이 규칙**
    이므로 시험이 시각을 정해야 한다.
    """
    row = IssueHistory(issue_id=issue_id, actor_id=None, changes=changes)
    session.add(row)
    await session.flush()
    if when is not None:
        row.created_at = when
        await session.flush()


async def move_state(
    session: AsyncSession, issue_id: UUID, world: dict[str, Any], to_name: str
) -> None:
    """상태를 옮기고 이력을 남긴다 — 서비스가 하는 것과 같은 모양으로."""
    issue = await session.get(Issue, issue_id)
    assert issue is not None
    current = await session.get(WorkflowState, issue.state_id)
    await record(
        session,
        issue_id,
        [{"field": "status", "from": current.name if current else None, "to": to_name}],
    )
    issue.state_id = world["states"][to_name].id
    await session.flush()


async def found(
    session: AsyncSession, permissions: PermissionService, world: dict[str, Any], iql: str
) -> set[str]:
    page = await SearchService(session, permissions).search(
        world["actor"], f"project = {world['project'].key} AND ({iql})", PageRequest(limit=50)
    )
    return {row.summary for row in page.items}


class TestWasIncludesTheCurrentValue:
    """**이 파일의 이유다.** 이력만 보면 안 바뀐 이슈가 통째로 빠진다."""

    async def test_an_issue_that_never_changed_still_matches_its_state(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        # 만들면 초기 상태(Open)다. 이력은 한 줄도 없다.
        await make(session, permissions, world, "그대로 있는 것")
        rows = list((await session.execute(select(IssueHistory))).scalars().all())
        assert rows == []

        assert await found(session, permissions, world, 'status WAS "Open"') == {"그대로 있는 것"}

    async def test_a_state_it_passed_through_and_left(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        issue = await make(session, permissions, world, "지나간 것")
        await move_state(session, issue, world, "In Progress")
        await move_state(session, issue, world, "Resolved")

        # 지금은 Resolved 인데 In Progress 를 지났다.
        assert await found(session, permissions, world, 'status WAS "In Progress"') == {"지나간 것"}
        # 떠나온 Open 도 이력의 `from` 이 증언한다.
        assert await found(session, permissions, world, 'status WAS "Open"') == {"지나간 것"}
        # 안 지난 것은 안 나온다.
        assert await found(session, permissions, world, 'status WAS "Closed"') == set()

    async def test_was_not_and_was_in(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        stayed = await make(session, permissions, world, "머문 것")
        moved = await make(session, permissions, world, "움직인 것")
        await move_state(session, moved, world, "In Progress")

        assert await found(session, permissions, world, 'status WAS NOT "In Progress"') == {
            "머문 것"
        }
        assert await found(
            session, permissions, world, 'status WAS IN ("In Progress", "Closed")'
        ) == {"움직인 것"}
        assert stayed is not None


class TestChangedIsOneChange:
    """**한 변경 안에서** x→y 여야 한다."""

    async def test_two_separate_changes_are_not_one_transition(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """Open→In Progress, In Progress→Resolved 를 거친 이슈에
        `FROM "Open" TO "Resolved"` 를 물으면 **아니다** 여야 한다.

        따로 일어난 두 변경을 이어 붙이면 없던 일을 있다고 답한다.
        """
        issue = await make(session, permissions, world, "두 걸음")
        await move_state(session, issue, world, "In Progress")
        await move_state(session, issue, world, "Resolved")

        assert (
            await found(session, permissions, world, 'status CHANGED FROM "Open" TO "Resolved"')
            == set()
        )
        # 한 걸음씩은 맞는다.
        assert await found(
            session, permissions, world, 'status CHANGED FROM "Open" TO "In Progress"'
        ) == {"두 걸음"}

    async def test_bare_changed_from_and_to(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        moved = await make(session, permissions, world, "옮긴 것")
        await make(session, permissions, world, "가만한 것")
        await move_state(session, moved, world, "In Progress")

        assert await found(session, permissions, world, "status CHANGED") == {"옮긴 것"}
        assert await found(session, permissions, world, 'status CHANGED TO "In Progress"') == {
            "옮긴 것"
        }
        assert await found(session, permissions, world, 'status CHANGED FROM "Open"') == {"옮긴 것"}

    async def test_assignee_changed_uses_ids(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """`assignee` 이력은 `assignee_id` 라는 이름 아래 UUID 문자열이다 —
        이름 대응이 틀리면 조건이 조용히 아무것도 안 맞는다."""
        issue = await make(session, permissions, world, "담당 바뀐 것")
        await record(
            session,
            issue,
            [
                {
                    "field": "assignee_id",
                    "from": None,
                    "to": str(world["other"].id),
                }
            ],
        )
        assert await found(
            session, permissions, world, f'assignee CHANGED TO "{world["other"].id}"'
        ) == {"담당 바뀐 것"}


class TestWindows:
    async def test_a_value_held_at_the_window_start_counts(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """**창 안에 변경이 없어도 참일 수 있다.** 창이 시작될 때 그 값이었고
        창 안에서 안 바뀌었으면, 그 창 동안 그 값이었던 것이다."""
        issue = await make(session, permissions, world, "창 전에 들어간 것")
        base = utcnow()
        # 아주 옛날에 In Progress 가 됐고, 그 뒤로 안 바뀌었다.
        await record(
            session,
            issue,
            [{"field": "status", "from": "Open", "to": "In Progress"}],
            when=base - timedelta(days=90),
        )
        row = await session.get(Issue, issue)
        assert row is not None
        row.state_id = world["states"]["In Progress"].id
        await session.flush()

        window = (
            f"DURING ({(base - timedelta(days=3)).date().isoformat()}, {base.date().isoformat()})"
        )
        assert await found(session, permissions, world, f'status WAS "In Progress" {window}') == {
            "창 전에 들어간 것"
        }

    async def test_a_change_outside_the_window_does_not_count(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        issue = await make(session, permissions, world, "창 밖에서 지난 것")
        base = utcnow()
        # 90일 전에 In Progress 를 지나 Resolved 로 갔다.
        await record(
            session,
            issue,
            [{"field": "status", "from": "Open", "to": "In Progress"}],
            when=base - timedelta(days=90),
        )
        await record(
            session,
            issue,
            [{"field": "status", "from": "In Progress", "to": "Resolved"}],
            when=base - timedelta(days=89),
        )
        row = await session.get(Issue, issue)
        assert row is not None
        row.state_id = world["states"]["Resolved"].id
        await session.flush()

        window = (
            f"DURING ({(base - timedelta(days=3)).date().isoformat()}, {base.date().isoformat()})"
        )
        assert (
            await found(session, permissions, world, f'status WAS "In Progress" {window}') == set()
        )
        # 창 안에서 갖고 있던 값은 맞는다.
        assert await found(session, permissions, world, f'status WAS "Resolved" {window}') == {
            "창 밖에서 지난 것"
        }

    async def test_after_and_before(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        old = await make(session, permissions, world, "옛 변경")
        recent = await make(session, permissions, world, "최근 변경")
        base = utcnow()
        await record(
            session,
            old,
            [{"field": "status", "from": "Open", "to": "In Progress"}],
            when=base - timedelta(days=60),
        )
        await record(
            session,
            recent,
            [{"field": "status", "from": "Open", "to": "In Progress"}],
            when=base - timedelta(days=1),
        )

        cut = (base - timedelta(days=7)).date().isoformat()
        assert await found(session, permissions, world, f"status CHANGED AFTER {cut}") == {
            "최근 변경"
        }
        assert await found(session, permissions, world, f"status CHANGED BEFORE {cut}") == {
            "옛 변경"
        }

    async def test_an_upside_down_window_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        with pytest.raises(iql_errors.IQLError):
            await found(
                session,
                permissions,
                world,
                'status WAS "Open" DURING (2026-06-01, 2026-01-01)',
            )


class TestRefusals:
    """물을 수 없는 것은 **거절한다.** 조용한 빈 결과는 "없다" 와 같게 생긴다."""

    async def test_a_field_without_resolvable_history_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        # `type` 이력에는 UUID 만 남는다 — 이름으로는 절대 안 맞는다.
        with pytest.raises(iql_errors.IQLError) as caught:
            await found(session, permissions, world, 'type WAS "Task"')
        assert "이력" in str(caught.value)

    async def test_custom_fields_are_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        with pytest.raises(iql_errors.IQLError):
            await found(session, permissions, world, 'cf["severity"] WAS "high"')

    async def test_an_unknown_field_is_still_an_unknown_field(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        with pytest.raises(iql_errors.IQLError):
            await found(session, permissions, world, 'nosuchfield WAS "x"')


class TestSuggestion:
    """**되는 자리에만 제안한다.**

    문법은 어느 필드 뒤에서든 `WAS` 를 받지만 컴파일러는 네 필드만 답한다.
    안 되는 자리에 제안하면 사람은 제안을 믿고 적었다가 거절당하고, 그러면
    다음부터 제안을 안 본다.
    """

    @staticmethod
    def _operators(text: str) -> list[str]:
        from ieum.modules.issues.iql.suggest import _operators, analyze

        ctx = analyze(text, len(text))
        assert ctx is not None
        return [s.label for s in _operators(ctx)]

    def test_history_fields_get_the_operators(self) -> None:
        for field_name in ("status", "assignee", "reporter", "priority"):
            labels = self._operators(f"{field_name} ")
            assert "WAS" in labels, field_name
            assert "CHANGED" in labels, field_name

    def test_other_fields_do_not(self) -> None:
        for field_name in ("summary", "created", "labels"):
            labels = self._operators(f"{field_name} ")
            assert "WAS" not in labels, field_name
            assert "CHANGED" not in labels, field_name

    def test_history_operators_come_after_the_common_ones(self) -> None:
        """흔한 것부터 보여야 한다. 목록 맨 위가 `WAS` 면 `=` 를 찾게 된다."""
        labels = self._operators("status ")
        assert labels.index("=") < labels.index("WAS")
