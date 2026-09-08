"""일괄 편집과 CSV 내보내기."""

from __future__ import annotations

import csv
import io
import secrets
from collections.abc import AsyncIterator
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
from ieum.modules.issues.bulk import MAX_BATCH, BulkService
from ieum.modules.issues.export import stream_csv, validate_export_query
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
    row = Project(key=f"B{secrets.token_hex(3).upper()}", name="Bulk Project")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"u-{new_id()}@example.com", display_name="Bulk Tester", status="active")
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
        granted=perms.project_scoped(),
        scope=Scope.project(project.id),
    )
    return actor_for(user)


async def make_issues(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    project: Project,
    issue_type: IssueType,
    count: int,
) -> list[UUID]:
    service = IssueService(session, permissions)
    out: list[UUID] = []
    for i in range(count):
        view = await service.create(
            actor,
            NewIssue(project_id=project.id, type_id=issue_type.id, summary=f"bulk {i}"),
        )
        out.append(view.issue.id)
    return out


class TestBulkEdit:
    async def test_applies_to_every_issue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        ids = await make_issues(session, permissions, actor, project, issue_type, 3)

        result = await BulkService(session, permissions).edit(actor, ids, changes={"priority": 1})
        assert result.all_succeeded
        assert set(result.updated) == set(ids)
        for issue_id in ids:
            row = await session.get(Issue, issue_id)
            assert row is not None
            assert row.priority == 1

    async def test_partial_failure_keeps_the_rest(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """한 건 때문에 99건이 막히면 안 되고, 조용히 넘어가도 안 된다."""
        actor = await full_access(session, user, project)
        ids = await make_issues(session, permissions, actor, project, issue_type, 2)
        missing = new_id()

        result = await BulkService(session, permissions).edit(
            actor, [*ids, missing], changes={"priority": 2}
        )
        assert set(result.updated) == set(ids)
        assert [f.issue_id for f in result.failed] == [missing]
        assert result.failed[0].code == "common.not_found"

    async def test_a_failure_does_not_poison_the_session(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """실패가 먼저 와도 뒤의 성공이 살아남아야 한다 (세이브포인트)."""
        actor = await full_access(session, user, project)
        ids = await make_issues(session, permissions, actor, project, issue_type, 2)

        result = await BulkService(session, permissions).edit(
            actor, [new_id(), *ids], changes={"priority": 5}
        )
        assert set(result.updated) == set(ids)
        assert len(result.failed) == 1

    async def test_labels_merge_per_issue(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """이슈마다 기존 라벨이 다르므로 클라이언트가 합칠 수 없다."""
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        first = await service.create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="a",
                labels=["keep", "drop"],
            ),
        )
        second = await service.create(
            actor,
            NewIssue(project_id=project.id, type_id=issue_type.id, summary="b", labels=["other"]),
        )

        await BulkService(session, permissions).edit(
            actor,
            [first.issue.id, second.issue.id],
            add_labels=["added"],
            remove_labels=["drop"],
        )
        assert (await service.get(actor, first.issue.id)).labels == ["added", "keep"]
        assert (await service.get(actor, second.issue.id)).labels == ["added", "other"]

    async def test_transition_in_bulk(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        service = IssueService(session, permissions)
        ids = await make_issues(session, permissions, actor, project, issue_type, 2)
        start = next(
            t
            for t in await service.available_transitions(actor, ids[0])
            if t.name == "Start progress"
        )

        result = await BulkService(session, permissions).edit(actor, ids, transition_id=start.id)
        assert result.all_succeeded
        for issue_id in ids:
            assert (await service.get(actor, issue_id)).state_category == "in_progress"

    async def test_rejects_an_oversized_batch(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        """상한이 없으면 사용자가 자기 서버를 멈춘다."""
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="까지 바꿀 수 있다"):
            await BulkService(session, permissions).edit(
                actor, [new_id() for _ in range(MAX_BATCH + 1)], changes={"priority": 1}
            )

    async def test_rejects_an_empty_change_set(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
    ) -> None:
        actor = await full_access(session, user, project)
        with pytest.raises(ValidationError, match="바꿀 내용이 없다"):
            await BulkService(session, permissions).edit(actor, [new_id()])

    async def test_duplicate_ids_are_applied_once(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        ids = await make_issues(session, permissions, actor, project, issue_type, 1)
        result = await BulkService(session, permissions).edit(
            actor, [ids[0], ids[0]], changes={"priority": 4}
        )
        assert result.updated == ids


class TestCsvExport:
    async def _collect(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        iql: str,
    ) -> list[list[str]]:
        chunks = [chunk async for chunk in stream_csv(session, permissions, actor, iql)]
        text = b"".join(chunks).decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    async def test_writes_a_header_and_rows(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        await make_issues(session, permissions, actor, project, issue_type, 3)
        await session.flush()

        rows = await self._collect(session, permissions, actor, f'project = "{project.key}"')
        assert rows[0][0] == "key"
        assert len(rows) == 4
        assert all(row[0].startswith(project.key) for row in rows[1:])

    async def test_starts_with_a_bom(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """BOM 이 없으면 엑셀이 UTF-8 을 로컬 인코딩으로 읽어 한국어가 깨진다."""
        actor = await full_access(session, user, project)
        await make_issues(session, permissions, actor, project, issue_type, 1)
        await session.flush()

        first = await anext(
            aiter(stream_csv(session, permissions, actor, f'project = "{project.key}"'))
        )
        assert first.startswith(b"\xef\xbb\xbf")

    async def test_quotes_values_that_would_break_the_format(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        actor = await full_access(session, user, project)
        await IssueService(session, permissions).create(
            actor,
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary='comma, "quote" and\nnewline',
            ),
        )
        await session.flush()

        rows = await self._collect(session, permissions, actor, f'project = "{project.key}"')
        assert rows[1][1] == 'comma, "quote" and\nnewline'

    async def test_refuses_an_empty_query(self) -> None:
        """빈 질의는 "볼 수 있는 이슈 전부" 라 설치 전체를 한 파일로 뽑는다."""
        with pytest.raises(ValidationError, match="범위를 좁혀야"):
            validate_export_query("   ")

    async def test_respects_the_acl(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        project: Project,
        issue_type: IssueType,
    ) -> None:
        """내보내기가 권한을 건너뛰면 그게 최악의 유출 경로다."""
        owner = User(email=f"o-{new_id()}@example.com", display_name="Owner", status="active")
        session.add(owner)
        await session.flush()
        owner_actor = await full_access(session, owner, project)
        await make_issues(session, permissions, owner_actor, project, issue_type, 2)
        await session.flush()

        outsider = actor_for(user)  # 아무 권한 없음
        rows = await self._collect(session, permissions, outsider, f'project = "{project.key}"')
        assert len(rows) == 1  # 헤더만
