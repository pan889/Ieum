"""IQL 파서·컴파일러·검색.

컴파일된 SQL 이 실제로 도는지까지 본다. 문자열만 비교하면
"그럴듯한 SQL 을 만들었지만 Postgres 가 거부하는" 경우를 놓친다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import Acl, PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql import errors as iql_errors
from ieum.modules.issues.iql.ast import And, Comparison, Not, Operator, Or
from ieum.modules.issues.iql.parser import parse
from ieum.modules.issues.iql.registry import FunctionContext
from ieum.modules.issues.models import (
    FieldDefinition,
    IssueType,
    Workflow,
    WorkflowState,
)
from ieum.modules.issues.search import SavedFilterService, SearchService, field_catalog
from ieum.modules.issues.service import IssueService, NewIssue
from ieum.modules.issues.workflow import DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

# ── 파서 (DB 불필요) ─────────────────────────────────────────────


class TestParser:
    def test_empty_query_has_no_condition(self) -> None:
        assert parse("   ").where is None

    def test_single_condition_is_not_wrapped(self) -> None:
        """Or(And(x)) 같은 껍데기는 컴파일러와 테스트를 둘 다 어렵게 만든다."""
        assert isinstance(parse("project = IEUM").where, Comparison)

    def test_and_or_precedence(self) -> None:
        """AND 가 OR 보다 강하게 묶인다."""
        node = parse("a = 1 OR b = 2 AND c = 3").where
        assert isinstance(node, Or)
        assert isinstance(node.operands[1], And)

    def test_parentheses_override_precedence(self) -> None:
        node = parse("(a = 1 OR b = 2) AND c = 3").where
        assert isinstance(node, And)
        assert isinstance(node.operands[0], Or)

    def test_not(self) -> None:
        assert isinstance(parse("NOT project = IEUM").where, Not)

    def test_keywords_are_case_insensitive(self) -> None:
        assert isinstance(parse("a = 1 and b = 2").where, And)
        assert isinstance(parse("a = 1 AnD b = 2").where, And)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("a = 1", Operator.EQ),
            ("a != 1", Operator.NE),
            ("a > 1", Operator.GT),
            ("a >= 1", Operator.GTE),
            ("a < 1", Operator.LT),
            ("a <= 1", Operator.LTE),
            ('a ~ "x"', Operator.CONTAINS),
            ('a !~ "x"', Operator.NOT_CONTAINS),
            ("a IN (1, 2)", Operator.IN),
            ("a NOT IN (1, 2)", Operator.NOT_IN),
        ],
    )
    def test_operators(self, text: str, expected: Operator) -> None:
        node = parse(text).where
        assert isinstance(node, Comparison) and node.operator is expected

    def test_custom_field_syntax(self) -> None:
        node = parse('cf["severity"] = high').where
        assert isinstance(node, Comparison)
        assert node.field.is_custom and node.field.custom_key == "severity"

    def test_order_by(self) -> None:
        query = parse("project = IEUM ORDER BY priority DESC, updated ASC")
        assert [(k.field.name, k.direction.value) for k in query.order_by] == [
            ("priority", "desc"),
            ("updated", "asc"),
        ]

    def test_quoted_strings_keep_spaces(self) -> None:
        node = parse('status = "In Progress"').where
        assert isinstance(node, Comparison)
        assert node.value.value == "In Progress"  # type: ignore[union-attr]

    def test_numbers_are_numbers_not_barewords(self) -> None:
        node = parse("priority > 3").where
        assert isinstance(node, Comparison)
        assert node.value.value == 3  # type: ignore[union-attr]

    def test_syntax_error_carries_offset(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            parse("project = ")
        assert exc.value.code == "iql.syntax_error"
        assert exc.value.details["offset"] > 0

    @pytest.mark.parametrize("text", ['status WAS "In Progress"', "assignee CHANGED FROM a TO b"])
    def test_history_operators_rejected_clearly(self, text: str) -> None:
        """미지원 기능은 조용히 무시하지 않는다. 언제 되는지까지 알려준다."""
        with pytest.raises(iql_errors.IQLError) as exc:
            parse(text)
        assert exc.value.code == "iql.unsupported"
        assert exc.value.details["milestone"] == "M5"

    def test_overlong_query_rejected(self) -> None:
        with pytest.raises(iql_errors.IQLError):
            parse("a = 1 AND " * 500)


# ── 컴파일 검증 (DB 불필요) ──────────────────────────────────────


def compile_only(text: str) -> None:
    from ieum.modules.issues.iql.compiler import compile_query

    compile_query(
        parse(text),
        acl=Acl(permission=perms.ISSUE_VIEW, is_global=True),
        ctx=FunctionContext(actor_id=new_id()),
    )


class TestValidation:
    def test_unknown_field_suggests_close_match(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("assigne = x")
        assert exc.value.code == "iql.unknown_field"
        assert "assignee" in exc.value.details["suggestions"]

    def test_unknown_function_suggests(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("assignee = currentUsr()")
        assert exc.value.code == "iql.unknown_function"
        assert "currentUser" in exc.value.details["suggestions"]

    def test_operator_not_allowed_for_type(self) -> None:
        """상태 이름에 부등호는 의미가 없다."""
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("status > x")
        assert exc.value.code == "iql.operator_not_allowed"
        assert "=" in exc.value.details["allowed"]

    def test_number_field_rejects_text(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("priority = high")
        assert exc.value.code == "iql.invalid_value"

    def test_date_field_rejects_garbage(self) -> None:
        with pytest.raises(iql_errors.IQLError):
            compile_only("due = notadate")

    def test_date_field_accepts_iso_and_functions(self) -> None:
        compile_only("due = 2026-09-06")
        compile_only("due <= endOfWeek()")
        compile_only("due >= startOfMonth(-1)")

    def test_function_arity_checked(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("assignee = currentUser(1)")
        assert exc.value.code == "iql.invalid_value"

    def test_function_return_type_checked(self) -> None:
        """날짜 함수를 사용자 필드에 쓰면 막는다."""
        with pytest.raises(iql_errors.IQLError):
            compile_only("assignee = now()")

    def test_in_requires_a_list(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("priority IN 3")
        assert exc.value.code == "iql.invalid_value"

    def test_empty_check_rejected_on_non_nullable(self) -> None:
        with pytest.raises(iql_errors.IQLError):
            compile_only("summary IS EMPTY")

    def test_unsortable_field_rejected(self) -> None:
        with pytest.raises(iql_errors.IQLError):
            compile_only("priority = 1 ORDER BY labels")

    def test_custom_field_sort_unsupported(self) -> None:
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only('priority = 1 ORDER BY cf["team"]')
        assert exc.value.code == "iql.unsupported"

    def test_errors_always_carry_position(self) -> None:
        for text in ["assigne = x", "status > x", "priority = high"]:
            with pytest.raises(iql_errors.IQLError) as exc:
                compile_only(text)
            assert "offset" in exc.value.details
            assert "length" in exc.value.details

    def test_unresolved_project_key_is_an_error_not_empty_result(self) -> None:
        """오타 난 프로젝트 키를 조용히 삼키면 "왜 결과가 없지"가 된다."""
        with pytest.raises(iql_errors.IQLError) as exc:
            compile_only("project = NOSUCHPROJECT")
        assert exc.value.code == "iql.invalid_value"
        assert exc.value.details["project"] == "NOSUCHPROJECT"

    def test_project_sort_is_out_of_scope_for_now(self) -> None:
        """키로 정렬하려면 org 테이블 조인이 필요해 모듈 경계를 넘는다."""
        with pytest.raises(iql_errors.IQLError):
            compile_only("priority = 1 ORDER BY project")

    def test_field_catalog_is_complete(self) -> None:
        catalog = {f["name"] for f in field_catalog()}
        assert {"project", "status", "assignee", "due", "labels"} <= catalog


# ── 실행 (실제 DB) ───────────────────────────────────────────────


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


@pytest_asyncio.fixture
async def fixture_set(
    session: AsyncSession, permissions: PermissionService
) -> AsyncIterator[dict[str, object]]:
    """검색용 데이터 한 벌: 프로젝트 1, 사용자 2, 이슈 4."""
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

    issue_type = IssueType(
        project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=workflow.id
    )
    session.add(issue_type)
    project = Project(key=f"Q{secrets.token_hex(3).upper()}", name="Query Test")
    session.add(project)
    owner = User(email=f"o-{new_id()}@e.com", display_name="Owner", status="active")
    other = User(email=f"x-{new_id()}@e.com", display_name="Other", status="active")
    session.add_all([owner, other])
    await session.flush()

    session.add(
        FieldDefinition(
            key="severity",
            name="Severity",
            kind="select",
            config={"options": ["low", "high"]},
        )
    )
    await session.flush()

    repo = RoleRepository(session)
    role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind="project")
    repo.add(role)
    await session.flush()
    for permission in (perms.ISSUE_VIEW, perms.ISSUE_CREATE, perms.ISSUE_ASSIGN):
        repo.grant(role.id, permission)
    for principal in (owner.id, other.id):
        repo.assign(
            role_id=role.id,
            scope=Scope.project(project.id),
            principal_kind="user",
            principal_id=principal,
        )
    await session.flush()

    actor = Actor(user_id=owner.id, email=owner.email, is_active=True, mfa_satisfied_at=utcnow())
    service = IssueService(session, permissions)
    today = datetime.now(UTC).date()

    made = {}
    made["a"] = await service.create(
        actor,
        NewIssue(
            project_id=project.id,
            type_id=issue_type.id,
            summary="로그인 실패",
            priority=1,
            assignee_id=owner.id,
            labels=["urgent", "auth"],
            due_date=today,
            custom_fields={"severity": "high"},
        ),
    )
    made["b"] = await service.create(
        actor,
        NewIssue(
            project_id=project.id,
            type_id=issue_type.id,
            summary="느린 검색",
            priority=3,
            assignee_id=other.id,
            labels=["perf"],
            due_date=today + timedelta(days=30),
            custom_fields={"severity": "low"},
        ),
    )
    made["c"] = await service.create(
        actor,
        NewIssue(project_id=project.id, type_id=issue_type.id, summary="담당자 없음", priority=5),
    )
    made["d"] = await service.create(
        actor,
        NewIssue(
            project_id=project.id,
            type_id=issue_type.id,
            summary="완료 예정",
            priority=2,
            labels=["urgent"],
        ),
    )
    await session.flush()

    yield {
        "project": project,
        "owner": owner,
        "other": other,
        "actor": actor,
        "issues": made,
        "states": states,
        "type": issue_type,
    }


async def run(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    iql: str,
    *,
    limit: int = 50,
) -> set[str]:
    page = await SearchService(session, permissions).search(actor, iql, PageRequest(limit=limit))
    return {i.summary for i in page.items}


@pytest.mark.integration
class TestExecution:
    async def test_no_condition_returns_visible_issues(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert len(await run(session, permissions, actor, "")) == 4  # type: ignore[arg-type]

    async def test_project_filter(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        project = fixture_set["project"]
        actor = fixture_set["actor"]
        found = await run(session, permissions, actor, f"project = {project.key}")  # type: ignore[union-attr,arg-type]
        assert len(found) == 4

    async def test_priority_range(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, "priority <= 2") == {  # type: ignore[arg-type]
            "로그인 실패",
            "완료 예정",
        }

    async def test_assignee_current_user(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, "assignee = currentUser()") == {  # type: ignore[arg-type]
            "로그인 실패"
        }

    async def test_assignee_is_empty(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, "assignee IS EMPTY") == {  # type: ignore[arg-type]
            "담당자 없음",
            "완료 예정",
        }

    async def test_labels_membership(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, 'labels = "urgent"') == {  # type: ignore[arg-type]
            "로그인 실패",
            "완료 예정",
        }

    async def test_labels_contains(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, 'labels ~ "urg"') == {  # type: ignore[arg-type]
            "로그인 실패",
            "완료 예정",
        }

    async def test_summary_contains_korean(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, 'summary ~ "검색"') == {"느린 검색"}  # type: ignore[arg-type]

    async def test_status_and_category(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert len(await run(session, permissions, actor, 'status = "Open"')) == 4  # type: ignore[arg-type]
        assert len(await run(session, permissions, actor, "statusCategory = todo")) == 4  # type: ignore[arg-type]
        assert await run(session, permissions, actor, "statusCategory = done") == set()  # type: ignore[arg-type]

    async def test_and_or_not_combination(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        found = await run(
            session,
            permissions,
            actor,
            'labels = "urgent" AND NOT priority = 1',  # type: ignore[arg-type]
        )
        assert found == {"완료 예정"}

    async def test_in_list(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, "priority IN (1, 5)") == {  # type: ignore[arg-type]
            "로그인 실패",
            "담당자 없음",
        }

    async def test_date_function_boundary(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        soon = await run(session, permissions, actor, "due <= endOfWeek()")  # type: ignore[arg-type]
        assert "로그인 실패" in soon
        assert "느린 검색" not in soon  # 30일 뒤

    async def test_custom_field_equality(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, 'cf["severity"] = high') == {  # type: ignore[arg-type]
            "로그인 실패"
        }

    async def test_custom_field_emptiness(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        assert await run(session, permissions, actor, 'cf["severity"] IS EMPTY') == {  # type: ignore[arg-type]
            "담당자 없음",
            "완료 예정",
        }

    async def test_order_by_changes_first_row(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        service = SearchService(session, permissions)
        asc = await service.search(actor, "ORDER BY priority ASC", PageRequest(limit=50))  # type: ignore[arg-type]
        desc = await service.search(
            actor,
            "ORDER BY priority DESC",
            PageRequest(limit=50),  # type: ignore[arg-type]
        )
        assert asc.items[0].priority == 1
        assert desc.items[0].priority == 5


@pytest.mark.integration
class TestAclEnforcement:
    async def test_results_are_scoped_to_visible_projects(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        """권한 없는 프로젝트의 이슈는 어떤 질의로도 나오면 안 된다."""
        hidden = Project(key=f"H{secrets.token_hex(3).upper()}", name="Hidden")
        session.add(hidden)
        await session.flush()

        insider = User(email=f"h-{new_id()}@e.com", display_name="H", status="active")
        session.add(insider)
        await session.flush()
        repo = RoleRepository(session)
        role = Role(name=f"h-{secrets.token_hex(4)}", scope_kind="project")
        repo.add(role)
        await session.flush()
        for permission in (perms.ISSUE_VIEW, perms.ISSUE_CREATE):
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id,
            scope=Scope.project(hidden.id),
            principal_kind="user",
            principal_id=insider.id,
        )
        await session.flush()

        insider_actor = Actor(
            user_id=insider.id,
            email=insider.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        await IssueService(session, permissions).create(
            insider_actor,
            NewIssue(
                project_id=hidden.id,
                type_id=fixture_set["type"].id,  # type: ignore[union-attr]
                summary="기밀",
            ),
        )
        await session.flush()

        # 조건 없는 질의로도, 명시적으로 지목해도 보이지 않는다.
        actor = fixture_set["actor"]
        assert "기밀" not in await run(session, permissions, actor, "")  # type: ignore[arg-type]
        assert "기밀" not in await run(
            session,
            permissions,
            actor,
            f"project = {hidden.key}",  # type: ignore[arg-type]
        )

    async def test_no_permissions_sees_nothing(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        stranger = User(email=f"n-{new_id()}@e.com", display_name="N", status="active")
        session.add(stranger)
        await session.flush()
        actor = Actor(
            user_id=stranger.id,
            email=stranger.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        assert await run(session, permissions, actor, "") == set()


@pytest.mark.integration
class TestSavedFilters:
    async def test_create_and_run(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        service = SavedFilterService(session, permissions)
        saved = await service.create(actor, name="내 이슈", iql="assignee = currentUser()")  # type: ignore[arg-type]
        page = await service.run(actor, saved.id, PageRequest(limit=50))  # type: ignore[arg-type]
        assert {i.summary for i in page.items} == {"로그인 실패"}

    async def test_invalid_iql_rejected_at_save(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        """깨진 필터를 저장하면 나중에 실행할 때 터진다."""
        actor = fixture_set["actor"]
        with pytest.raises(iql_errors.IQLError):
            await SavedFilterService(session, permissions).create(
                actor,
                name="깨짐",
                iql="project = ",  # type: ignore[arg-type]
            )

    async def test_runs_with_runner_permissions_not_owners(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        """공유 필터가 소유자 권한을 승계하면 공유가 곧 권한 상승이 된다."""
        owner_actor = fixture_set["actor"]
        service = SavedFilterService(session, permissions)
        shared = await service.create(
            owner_actor,
            name="전체",
            iql="",
            is_shared=True,  # type: ignore[arg-type]
        )

        stranger = User(email=f"s-{new_id()}@e.com", display_name="S", status="active")
        session.add(stranger)
        await session.flush()
        stranger_actor = Actor(
            user_id=stranger.id,
            email=stranger.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        page = await service.run(stranger_actor, shared.id, PageRequest(limit=50))
        assert page.items == []  # 실행자에게 권한이 없으므로 빈 결과

    async def test_private_filter_hidden_from_others(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        from ieum.core.exceptions import NotFoundError

        owner_actor = fixture_set["actor"]
        service = SavedFilterService(session, permissions)
        private = await service.create(owner_actor, name="비공개", iql="priority = 1")  # type: ignore[arg-type]

        other = fixture_set["other"]
        other_actor = Actor(
            user_id=other.id,  # type: ignore[union-attr]
            email=other.email,  # type: ignore[union-attr]
            is_active=True,
            mfa_satisfied_at=utcnow(),
        )
        # 존재 자체를 숨긴다. 남의 필터 이름을 열거할 수 있으면 안 된다.
        with pytest.raises(NotFoundError):
            await service.get(other_actor, private.id)

    async def test_duplicate_name_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        from ieum.core.exceptions import ConflictError

        actor = fixture_set["actor"]
        service = SavedFilterService(session, permissions)
        await service.create(actor, name="같은 이름", iql="priority = 1")  # type: ignore[arg-type]
        with pytest.raises(ConflictError):
            await service.create(actor, name="같은 이름", iql="priority = 2")  # type: ignore[arg-type]


@pytest.mark.integration
class TestValidateApi:
    async def test_valid_query_reports_fields(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        actor = fixture_set["actor"]
        result = await SearchService(session, permissions).validate(
            actor,
            "assignee = currentUser() ORDER BY priority DESC",  # type: ignore[arg-type]
        )
        assert result.valid
        assert set(result.fields or []) == {"assignee", "priority"}

    async def test_invalid_query_reports_position(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        fixture_set: dict[str, object],
    ) -> None:
        """타이핑 중 호출되므로 예외가 아니라 결과로 돌려준다."""
        actor = fixture_set["actor"]
        result = await SearchService(session, permissions).validate(actor, "assigne = x")  # type: ignore[arg-type]
        assert not result.valid
        assert result.error is not None
        assert result.error["code"] == "iql.unknown_field"
        assert "offset" in result.error
