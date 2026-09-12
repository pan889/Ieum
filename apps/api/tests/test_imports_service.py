"""이관 미리 보기의 서비스 계층.

HTTP 시험(`test_imports_api.py`)이 못 고정하는 것이 여기 있다: **step-up 을
실제로 요구하는가.** API 쪽에서는 2FA 를 통과하지 않은 세션이 애초에 무엇도
못 하므로(그 토큰은 모든 경로에서 403 이다), 거기서 403 을 봐도 step-up 이
이유인지 알 수 없다 — 실제로 처음에 그런 시험을 썼다가 `requires_step_up` 을
빼도 초록인 것을 보고 알았다.

여기서는 액터를 직접 짓는다. **MFA 를 등록하지 않은 관리자**가 이 일을
못 하는 것이 이 파일이 지키는 것이다.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StepUpRequiredError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.migrate.archive import Archive, Issue, Manifest, Source, write_archive
from ieum.migrate.archive import Project as SourceProject
from ieum.modules.identity.models import User
from ieum.modules.imports import permissions as perms
from ieum.modules.imports.service import ImportService
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


async def _admin(session: AsyncSession) -> User:
    person = User(email=f"im-{new_id()}@example.com", display_name="이관 담당", status="active")
    session.add(person)
    await session.flush()
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=(perms.IMPORT_RUN,),
        scope=Scope.global_(),
    )
    return person


async def _project(session: AsyncSession, *, archived: bool = False) -> Project:
    row = Project(key=f"P{new_id().hex[:6].upper()}", name="받는 프로젝트")
    if archived:
        row.archived_at = utcnow()
    session.add(row)
    await session.flush()
    return row


def _bundle() -> bytes:
    return write_archive(
        Archive(
            manifest=Manifest(
                source=Source(kind="redmine"),
                project=SourceProject(key="p", name="소스"),
                taken_at="2026-09-12T00:00:00Z",
            ),
            people=[],
            issues=[Issue(source_id="1", summary="하나", type="Bug", status="Open")],
        )
    )


async def _two_workflow_project(session: AsyncSession) -> Project:
    """워크플로우가 둘인 프로젝트.

    Bug 는 `버그` 워크플로우(Open·Closed), Story 는 `기획` 워크플로우
    (Backlog·Done). 상태 목록은 넷을 한 통에 담아 내므로, 이름만 보고 고르면
    Story 에 Open 이 걸릴 수 있다.
    """
    from ieum.modules.issues.models import IssueType, Workflow, WorkflowState

    row = Project(key=f"W{new_id().hex[:6].upper()}", name="워크플로우 둘")
    session.add(row)
    await session.flush()
    for wf_name, states, type_name in (
        (f"버그-{new_id().hex[:4]}", (("Open", "todo", True), ("Closed", "done", False)), "Bug"),
        (
            f"기획-{new_id().hex[:4]}",
            (("Backlog", "todo", True), ("Done", "done", False)),
            "Story",
        ),
    ):
        workflow = Workflow(name=wf_name)
        session.add(workflow)
        await session.flush()
        for position, (name, category, initial) in enumerate(states):
            session.add(
                WorkflowState(
                    workflow_id=workflow.id,
                    name=name,
                    category=category,
                    position=position,
                    is_initial=initial,
                )
            )
        session.add(IssueType(project_id=row.id, name=type_name, workflow_id=workflow.id))
    await session.flush()
    return row


def _bundle_of(issues: list[Issue]) -> bytes:
    return write_archive(
        Archive(
            manifest=Manifest(
                source=Source(kind="redmine"),
                project=SourceProject(key="p", name="소스"),
                taken_at="2026-09-12T00:00:00Z",
            ),
            people=[],
            issues=issues,
        )
    )


class TestCrossedWorkflows:
    """**만들어지기는 하는데 아무도 못 옮기는 이슈.**

    상태 목록은 프로젝트의 모든 워크플로우를 한 통에 담아 보여 준다. 워크플로우가
    둘 이상이면 이름만 보고 고른 상태가 다른 워크플로우의 것일 수 있고, 그대로
    실으면 그 상태에서 나갈 전이가 하나도 없다. `IssueTypeRef` 가 `workflow_id`
    를 함께 내는 이유가 이 판단인데, 아무도 보지 않고 있었다.
    """

    async def test_a_status_from_another_workflow_blocks_the_load(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _two_workflow_project(session)

        preview = await ImportService(session, permissions).preview(
            actor_for(person),
            project_id=project.id,
            # Story 는 `기획` 워크플로우인데 Open 은 `버그` 쪽 상태다.
            data=_bundle_of([Issue(source_id="1", summary="하나", type="Story", status="Open")]),
        )
        assert preview.can_load is False
        assert any("Story" in line and "Open" in line for line in preview.report.blocking)

    async def test_a_matching_pair_is_fine(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """거르는 것이 지나쳐 맞는 짝까지 막으면 아무것도 못 옮긴다."""
        person = await _admin(session)
        project = await _two_workflow_project(session)

        preview = await ImportService(session, permissions).preview(
            actor_for(person),
            project_id=project.id,
            data=_bundle_of(
                [
                    Issue(source_id="1", summary="하나", type="Story", status="Backlog"),
                    Issue(source_id="2", summary="둘", type="Bug", status="Open"),
                ]
            ),
        )
        assert preview.report.crossed_workflows == []
        assert preview.can_load is True


class TestStepUp:
    async def test_an_admin_without_two_factor_cannot_run_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**시각만으로는 부족하다.** MFA 를 등록하지 않은 계정도 로그인하면
        `mfa_satisfied_at` 이 채워진다. 실제로 통과했는지를 봐야 한다."""
        person = await _admin(session)
        project = await _project(session)
        weak = Actor(
            user_id=person.id,
            email=person.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
            mfa_verified=False,
        )

        # **`PermissionDeniedError` 가 아니다.** 권한은 있고 2단계 인증이
        # 없는 것이라, 화면이 "권한이 없다" 대신 "2FA 를 등록하라" 를 띄울 수
        # 있어야 한다. 둘을 한 예외로 뭉치면 그 안내를 못 한다.
        with pytest.raises(StepUpRequiredError):
            await ImportService(session, permissions).preview(
                weak, project_id=project.id, data=_bundle()
            )

    async def test_the_same_person_with_two_factor_can(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)

        preview = await ImportService(session, permissions).preview(
            actor_for(person), project_id=project.id, data=_bundle()
        )
        assert preview.counted.issues == 1

    async def test_someone_without_the_permission_cannot(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        stranger = User(email=f"s-{new_id()}@example.com", display_name="남", status="active")
        session.add(stranger)
        await session.flush()
        project = await _project(session)

        with pytest.raises(PermissionDeniedError):
            await ImportService(session, permissions).preview(
                actor_for(stranger), project_id=project.id, data=_bundle()
            )


class TestWhereItRefusesToStart:
    async def test_an_archived_project_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """접은 프로젝트로 옮기면 옮긴 것이 아무에게도 안 보인다."""
        person = await _admin(session)
        project = await _project(session, archived=True)

        with pytest.raises(ConflictError):
            await ImportService(session, permissions).preview(
                actor_for(person), project_id=project.id, data=_bundle()
            )

    async def test_a_project_that_is_not_there(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        with pytest.raises(NotFoundError):
            await ImportService(session, permissions).preview(
                actor_for(person), project_id=new_id(), data=_bundle()
            )


class TestItSavesNothing:
    async def test_previewing_twice_still_says_nothing_is_here(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**미리 보기는 몇 번이고 돌려 볼 수 있어야 한다.** 짝을 바꿔 가며
        보는 것이 이 화면의 쓸모인데, 한 번 보는 것만으로 무언가 남으면
        두 번째 화면이 첫 번째와 다른 말을 한다."""
        person = await _admin(session)
        project = await _project(session)
        service = ImportService(session, permissions)

        first = await service.preview(actor_for(person), project_id=project.id, data=_bundle())
        second = await service.preview(actor_for(person), project_id=project.id, data=_bundle())
        assert first.counted.already_here == 0
        assert second.counted.already_here == 0
