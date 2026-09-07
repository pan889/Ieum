"""커스텀 필드 정의와 워크플로우 조회 (관리 콘솔).

필드 정의를 넣는 화면이 없어서 `python -m ieum.cli seed-fields` 가 유일한
길이었다. 여기서 고정하는 것은 두 가지다: **채울 수 없는 필드를 만들지
못하게 하는가**, 그리고 **지운 값이 되살아나지 않는가.**
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.admin import FieldDefinitionService, WorkflowCatalogService
from ieum.modules.issues.models import (
    Issue,
    IssueFieldValue,
    IssueType,
    Workflow,
    WorkflowState,
    WorkflowTransition,
)
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


async def _person(session: AsyncSession) -> User:
    row = User(email=f"p-{new_id()}@example.com", display_name="Person", status="active")
    session.add(row)
    await session.flush()
    return row


async def _admin(session: AsyncSession, *granted: str) -> User:
    person = await _person(session)
    await grant(session, principal_id=person.id, permissions_granted=granted, scope=Scope.global_())
    return person


class TestPermission:
    async def test_a_stranger_cannot_see_the_definitions(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await FieldDefinitionService(session, permissions).list_all(actor_for(nobody))

    async def test_a_stranger_cannot_see_the_workflows(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await WorkflowCatalogService(session, permissions).list_all(actor_for(nobody))


class TestCreate:
    async def test_the_key_is_normalized(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """키는 소문자다 — DB CHECK 가 요구하고, IQL 이 식별자로 받아 쓴다.
        받아서 깎지 않으면 대문자로 적은 사람은 500 을 본다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)

        definition = await service.create(
            actor,
            key="  CF_Severity  ",
            name="  Severity  ",
            kind="select",
            config={"options": ["low", "high"]},
        )
        assert definition.key == "cf_severity"
        assert definition.name == "Severity"

    async def test_the_same_key_twice_is_a_conflict_not_a_crash(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        await service.create(actor, key="cf_notes", name="Notes", kind="text")

        with pytest.raises(ConflictError) as exc:
            await service.create(actor, key="cf_notes", name="Other", kind="text")
        assert exc.value.code == "issues.field_key_taken"

    @pytest.mark.parametrize("key", ["1abc", "cf-severity", "x", "cf severity", "", "cf!"])
    async def test_bad_keys_are_refused(
        self, session: AsyncSession, permissions: PermissionService, key: str
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        with pytest.raises(ValidationError) as exc:
            await FieldDefinitionService(session, permissions).create(
                actor, key=key, name="X", kind="text"
            )
        assert exc.value.code == "issues.invalid_field_key"

    async def test_an_unknown_kind_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """DB CHECK 도 막지만, 여기서 막으면 어떤 종류가 있는지 알려줄 수 있다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        with pytest.raises(ValidationError) as exc:
            await FieldDefinitionService(session, permissions).create(
                actor, key="cf_x", name="X", kind="rating"
            )
        assert exc.value.code == "issues.unknown_field_kind"
        assert "text" in exc.value.details["kinds"]


class TestChoicesMustExist:
    """선택지가 없는 `select` 는 **채울 수 없는 필드**다.

    저장을 받아 주면 화면은 빈 드롭다운을 그리고, 필수로 걸어 두었으면 그
    프로젝트의 이슈를 아무도 저장할 수 없게 된다.
    """

    @pytest.mark.parametrize("kind", ["select", "multi_select"])
    async def test_empty_choices_are_refused(
        self, session: AsyncSession, permissions: PermissionService, kind: str
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)

        with pytest.raises(ValidationError) as exc:
            await service.create(actor, key="cf_pick", name="Pick", kind=kind, config={})
        assert exc.value.code == "issues.field_options_required"

        with pytest.raises(ValidationError):
            await service.create(
                actor, key="cf_pick", name="Pick", kind=kind, config={"options": []}
            )

    async def test_duplicate_choices_are_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """같은 선택지가 둘이면 화면에서 어느 것을 골랐는지 알 수 없다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        with pytest.raises(ValidationError) as exc:
            await FieldDefinitionService(session, permissions).create(
                actor,
                key="cf_pick",
                name="Pick",
                kind="select",
                config={"options": ["a", "b", "a"]},
            )
        assert exc.value.code == "issues.field_options_duplicated"

    async def test_editing_them_away_is_refused_too(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """만들 때만 보면, 나중에 비워서 같은 상태를 만들 수 있다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(
            actor, key="cf_pick", name="Pick", kind="select", config={"options": ["a"]}
        )

        with pytest.raises(ValidationError) as exc:
            await service.update(actor, definition.id, config={"options": []})
        assert exc.value.code == "issues.field_options_required"


class TestUpdate:
    async def test_the_config_is_replaced_not_merged(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(
            actor,
            key="cf_pick",
            name="Pick",
            kind="select",
            config={"options": ["a", "b"], "stale": 1},
        )

        updated = await service.update(actor, definition.id, config={"options": ["a"]})
        assert updated.config == {"options": ["a"]}

    async def test_the_label_and_required_flag_can_change(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(actor, key="cf_notes", name="Notes", kind="text")

        updated = await service.update(
            actor, definition.id, name="Internal notes", is_required=True, position=3
        )
        assert (updated.name, updated.is_required, updated.position) == ("Internal notes", True, 3)
        # 키와 종류는 바꿀 길이 없다. 값의 주소와 해석이기 때문이다.
        assert (updated.key, updated.kind) == ("cf_notes", "text")

    async def test_an_empty_label_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(actor, key="cf_notes", name="Notes", kind="text")

        with pytest.raises(ValidationError) as exc:
            await service.update(actor, definition.id, name="   ")
        assert exc.value.code == "issues.field_name_required"

    async def test_an_unknown_definition_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        with pytest.raises(NotFoundError):
            await FieldDefinitionService(session, permissions).update(actor, new_id(), name="X")


class TestDeleteTakesTheValuesWithIt:
    """`issue_field_value.field_key` 에는 FK 가 없다.

    정의만 지우면 값은 보이지 않는 채로 남고, 나중에 같은 키로 정의를 다시
    만들면 그 값이 **다시 나타난다** — 종류가 달라졌으면 위젯이 엉뚱한 것을
    그린다. 그룹을 지울 때 역할 할당을 함께 지우는 것과 같은 판단이다.
    """

    async def _issue_with_value(self, session: AsyncSession, key: str, value: object) -> Issue:
        workflow = Workflow(name=f"wf-{new_id()}")
        session.add(workflow)
        await session.flush()
        state = WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", is_initial=True
        )
        session.add(state)
        issue_type = IssueType(name="Task", workflow_id=workflow.id)
        project = Project(key=f"P{new_id().hex[:4].upper()}", name="P")
        session.add_all([issue_type, project])
        await session.flush()

        issue = Issue(
            project_id=project.id,
            key_seq=1,
            summary="X",
            type_id=issue_type.id,
            state_id=state.id,
        )
        session.add(issue)
        await session.flush()
        session.add(IssueFieldValue(issue_id=issue.id, field_key=key, value=value))
        await session.flush()
        return issue

    async def test_the_count_is_visible_before_deleting(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """지우기 전에 몇 개가 사라지는지 알아야 한다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(actor, key="cf_notes", name="Notes", kind="text")
        await self._issue_with_value(session, "cf_notes", "hello")

        views = {v.definition.id: v for v in await service.list_all(actor)}
        assert views[definition.id].values == 1

    async def test_the_values_go_with_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        definition = await service.create(actor, key="cf_notes", name="Notes", kind="text")
        await self._issue_with_value(session, "cf_notes", "hello")

        removed = await service.delete(actor, definition.id)
        assert removed == 1
        await session.flush()

        rows = (
            await session.execute(
                select(IssueFieldValue).where(IssueFieldValue.field_key == "cf_notes")
            )
        ).scalars()
        assert list(rows) == []

    async def test_other_fields_are_untouched(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """키로 지우므로 범위를 틀리면 남의 값까지 지운다."""
        actor = actor_for(await _admin(session, perms.FIELD_MANAGE))
        service = FieldDefinitionService(session, permissions)
        doomed = await service.create(actor, key="cf_doomed", name="Doomed", kind="text")
        await service.create(actor, key="cf_keeper", name="Keeper", kind="text")

        issue = await self._issue_with_value(session, "cf_doomed", "x")
        session.add(IssueFieldValue(issue_id=issue.id, field_key="cf_keeper", value="y"))
        await session.flush()

        await service.delete(actor, doomed.id)
        await session.flush()

        rows = (
            await session.execute(
                select(IssueFieldValue.field_key).where(IssueFieldValue.issue_id == issue.id)
            )
        ).scalars()
        assert list(rows) == ["cf_keeper"]


class TestWorkflowCatalogue:
    async def _workflow(self, session: AsyncSession) -> Workflow:
        workflow = Workflow(name=f"wf-{new_id()}", description="d")
        session.add(workflow)
        await session.flush()
        open_state = WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
        done_state = WorkflowState(
            workflow_id=workflow.id, name="Done", category="done", position=1
        )
        session.add_all([open_state, done_state])
        await session.flush()
        session.add_all(
            [
                WorkflowTransition(
                    workflow_id=workflow.id,
                    name="Finish",
                    from_state_id=open_state.id,
                    to_state_id=done_state.id,
                ),
                # 전역 전이. `from` 이 비어 있으면 어디서든 갈 수 있다.
                WorkflowTransition(
                    workflow_id=workflow.id,
                    name="Reopen",
                    from_state_id=None,
                    to_state_id=open_state.id,
                ),
            ]
        )
        session.add(IssueType(name="Task", workflow_id=workflow.id))
        await session.flush()
        return workflow

    async def test_the_list_counts_states_and_transitions(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.WORKFLOW_MANAGE))
        workflow = await self._workflow(session)

        views = {
            v.workflow.id: v
            for v in await WorkflowCatalogService(session, permissions).list_all(actor)
        }
        mine = views[workflow.id]
        assert (mine.states, mine.transitions) == (2, 2)
        # 어떤 이슈 유형이 쓰는지. 고칠 때의 영향 범위다.
        assert mine.used_by == ["Task"]

    async def test_the_detail_includes_global_transitions(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """전역 전이를 빼면 전이 목록이 거짓말을 한다 — 어디서든 갈 수 있는
        길이 안 보인다."""
        actor = actor_for(await _admin(session, perms.WORKFLOW_MANAGE))
        workflow = await self._workflow(session)

        detail = await WorkflowCatalogService(session, permissions).detail(actor, workflow.id)
        assert [s.name for s in detail.states] == ["Open", "Done"]
        globals_ = [t for t in detail.transitions if t.from_state_id is None]
        assert [t.name for t in globals_] == ["Reopen"]

    async def test_an_unknown_workflow_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = actor_for(await _admin(session, perms.WORKFLOW_MANAGE))
        with pytest.raises(NotFoundError):
            await WorkflowCatalogService(session, permissions).detail(actor, new_id())
