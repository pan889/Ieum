"""리포트 — IQL 집계 (A29, M5). 실제 Postgres 를 쓴다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **칸의 합이 총계와 맞는다.** 안 맞으면 리포트가 거짓말을 하고, 사람은 대개
  큰 쪽을 믿는다. 여럿에 걸리는 기준(라벨)만 예외이고, 그때는 그렇다고
  응답이 말한다.
- **담당자 없음은 빈 칸이 아니라 하나의 칸이다.** 빼면 총계가 안 맞고, 보통
  가장 봐야 할 무리가 사라진다.
- 조건에 이미 쓰인 필드로 묶어도 조인이 두 번 걸리지 않는다 — 걸리면 행이
  곱해져 세는 값이 조용히 커진다.
- ACL 은 검색과 같은 길을 탄다. 안 보여야 할 이슈가 숫자로 새면 안 된다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import (
    Issue,
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.issues.reports import MAX_BUCKETS, ReportService
from ieum.modules.issues.service import IssueService, NewIssue, SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_TRANSITIONS, DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    return service


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
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
    await session.flush()
    for position, (name, from_name, to_name, post) in enumerate(DEFAULT_TRANSITIONS):
        session.add(
            WorkflowTransition(
                workflow_id=workflow.id,
                name=name,
                from_state_id=states[from_name].id if from_name else None,
                to_state_id=states[to_name].id,
                conditions=[],
                post_functions=post,
                position=position,
            )
        )
    issue_type = IssueType(
        project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=workflow.id
    )
    session.add(issue_type)
    project = Project(key=f"R{secrets.token_hex(3).upper()}", name="Report Test")
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

    yield {
        "project": project,
        "type": issue_type,
        "states": states,
        "actor": Actor(
            user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow()
        ),
        "user": user,
        "other": other,
    }


async def make(
    session: AsyncSession,
    permissions: PermissionService,
    world: dict[str, Any],
    summary: str,
    *,
    assignee: UUID | None = None,
    priority: int | None = None,
    labels: list[str] | None = None,
) -> UUID:
    view = await IssueService(session, permissions).create(
        world["actor"],
        NewIssue(
            project_id=world["project"].id,
            type_id=world["type"].id,
            summary=summary,
            assignee_id=assignee,
            labels=labels or [],
        ),
    )
    if priority is not None:
        view.issue.priority = priority
        await session.flush()
    return view.issue.id


async def counted(
    session: AsyncSession,
    permissions: PermissionService,
    world: dict[str, Any],
    group_by: str,
    iql: str | None = None,
) -> Any:
    return await ReportService(session, permissions).count(
        world["actor"],
        iql=iql if iql is not None else f"project = {world['project'].key}",
        group_by=group_by,
    )


class TestTheSumMatchesTheTotal:
    """**이 파일의 이유다.** 안 맞으면 사람은 큰 쪽을 믿는다."""

    @pytest.mark.parametrize(
        "group_by", ["status", "statuscategory", "assignee", "priority", "type", "sprint"]
    )
    async def test_single_valued_groupings_add_up(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
        group_by: str,
    ) -> None:
        await make(session, permissions, world, "가", assignee=world["user"].id, priority=1)
        await make(session, permissions, world, "나", priority=1)
        await make(session, permissions, world, "다", assignee=world["other"].id, priority=5)

        report = await counted(session, permissions, world, group_by)
        assert report.multi_valued is False
        assert sum(b.count for b in report.buckets) == report.total == 3

    async def test_labels_may_exceed_the_total_and_say_so(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """이슈 하나가 라벨 셋을 달면 세 칸에 들어간다. **그건 사실이다** —
        숨기지 않고 `multi_valued` 로 적는다."""
        await make(session, permissions, world, "셋", labels=["a", "b", "c"])
        await make(session, permissions, world, "없음")

        report = await counted(session, permissions, world, "labels")
        assert report.multi_valued is True
        assert report.total == 2
        assert sum(b.count for b in report.buckets) == 4  # a, b, c, (없음)


class TestEmptyIsABucket:
    async def test_unassigned_issues_are_their_own_bucket(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """**보통 가장 봐야 할 무리다.** 빼면 총계가 안 맞는다."""
        await make(session, permissions, world, "맡은 것", assignee=world["user"].id)
        await make(session, permissions, world, "안 맡은 것 1")
        await make(session, permissions, world, "안 맡은 것 2")

        report = await counted(session, permissions, world, "assignee")
        empty = [b for b in report.buckets if b.key is None]
        assert len(empty) == 1
        assert empty[0].count == 2
        assert sum(b.count for b in report.buckets) == report.total == 3

    async def test_backlog_is_a_sprint_bucket(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        await make(session, permissions, world, "백로그에 있는 것")
        report = await counted(session, permissions, world, "sprint")
        assert [(b.key, b.count) for b in report.buckets] == [(None, 1)]


class TestJoinsAreNotDoubled:
    async def test_grouping_by_a_field_the_query_already_uses(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """조건이 `status` 를 쓰면 컴파일러가 이미 조인해 뒀다.

        묶으려고 또 걸면 같은 테이블이 두 번 들어가 행이 곱해지고, **세는
        값이 조용히 커진다.**
        """
        await make(session, permissions, world, "하나")
        await make(session, permissions, world, "둘")

        report = await counted(
            session,
            permissions,
            world,
            "status",
            iql=f'project = {world["project"].key} AND status = "Open"',
        )
        assert sum(b.count for b in report.buckets) == report.total == 2

    async def test_grouping_by_labels_when_the_query_filters_labels(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        await make(session, permissions, world, "라벨 있음", labels=["keep"])
        await make(session, permissions, world, "라벨 없음")

        report = await counted(
            session,
            permissions,
            world,
            "labels",
            iql=f'project = {world["project"].key} AND labels = "keep"',
        )
        assert report.total == 1
        assert [(b.key, b.count) for b in report.buckets] == [("keep", 1)]


class TestAclAndRefusals:
    async def test_a_stranger_counts_nothing(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """세는 길이 권한을 우회하면 **안 보여야 할 이슈가 숫자로 샌다.**"""
        await make(session, permissions, world, "비밀")
        stranger = User(email=f"s-{new_id()}@e.com", display_name="남", status="active")
        session.add(stranger)
        await session.flush()
        outsider = Actor(
            user_id=stranger.id,
            email=stranger.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        report = await ReportService(session, permissions).count(
            outsider, iql=f"project = {world['project'].key}", group_by="status"
        )
        assert report.total == 0
        assert report.buckets == []

    async def test_an_unknown_grouping_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        world: dict[str, Any],
    ) -> None:
        """`summary` 로 묶으면 칸이 이슈 수만큼 나온다 — 그건 리포트가 아니라
        목록이다. 모르는 기준은 조용히 빈 결과를 주지 않고 거절한다."""
        with pytest.raises(ValidationError):
            await counted(session, permissions, world, "summary")
        with pytest.raises(ValidationError):
            await counted(session, permissions, world, "없는기준")

    async def test_the_supported_list_is_not_empty(self) -> None:
        """비어 있으면 위 시험들이 무의미해진다."""
        from ieum.modules.issues.reports import GROUPS

        assert len(GROUPS) >= 6
        assert MAX_BUCKETS > 0
