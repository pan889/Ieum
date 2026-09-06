"""core.permissions — 권한 커널.

리졸버는 org 모듈이 구현하므로 여기서는 가짜 리졸버로 커널 로직만 검증한다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import PermissionDeniedError, StepUpRequiredError
from ieum.core.ids import new_id
from ieum.core.permissions import (
    Acl,
    PermissionDef,
    PermissionRegistry,
    PermissionService,
    Scope,
    ScopeKind,
    registry,
)
from ieum.core.time import utcnow

ISSUE_VIEW = "test.issue.view"
ISSUE_EDIT = "test.issue.edit"
IDP_MANAGE = "test.admin.idp.manage"

registry.register_many(
    [
        PermissionDef(ISSUE_VIEW, frozenset({ScopeKind.PROJECT}), "이슈 조회"),
        PermissionDef(ISSUE_EDIT, frozenset({ScopeKind.PROJECT}), "이슈 수정"),
        PermissionDef(IDP_MANAGE, frozenset({ScopeKind.GLOBAL}), "IdP 관리", requires_step_up=True),
    ]
)


class FakeResolver:
    """역할 조회를 흉내 낸다. 호출 횟수를 세어 요청 스코프 캐시를 검증한다."""

    def __init__(self, grants: dict[Scope, set[str]]) -> None:
        self._grants = grants
        self.calls = 0

    async def permissions_in_scope(
        self, session: AsyncSession, actor: Actor, scope: Scope
    ) -> frozenset[str]:
        self.calls += 1
        return frozenset(self._grants.get(scope, set()))

    async def acl_for(self, session: AsyncSession, actor: Actor, permission: str) -> Acl:
        self.calls += 1
        projects = {
            s.id
            for s, perms in self._grants.items()
            if s.kind is ScopeKind.PROJECT and permission in perms and s.id
        }
        return Acl(
            permission=permission,
            is_global=permission in self._grants.get(Scope.global_(), set()),
            project_ids=frozenset(projects),
        )


@pytest.fixture
def project_id() -> UUID:
    return new_id()


@pytest.fixture
def service(project_id: UUID) -> PermissionService:
    resolver = FakeResolver({Scope.project(project_id): {ISSUE_VIEW}})
    return PermissionService(resolver=resolver, step_up_window_seconds=300)


class TestScope:
    def test_global_rejects_id(self) -> None:
        with pytest.raises(ValueError, match="id 를 갖지 않는다"):
            Scope(ScopeKind.GLOBAL, new_id())

    def test_scoped_requires_id(self) -> None:
        with pytest.raises(ValueError, match="id 가 필요하다"):
            Scope(ScopeKind.PROJECT, None)


class TestRegistry:
    def test_unknown_permission_raises(self) -> None:
        """오타 난 권한이 '항상 거부'로 조용히 굴러가면 안 된다."""
        with pytest.raises(KeyError, match="등록되지 않은 권한"):
            PermissionRegistry().get("nope.nope")

    def test_conflicting_redefinition_rejected(self) -> None:
        r = PermissionRegistry()
        r.register(PermissionDef("a.b", frozenset({ScopeKind.GLOBAL}), "x"))
        with pytest.raises(ValueError, match="다른 정의로 이미 등록"):
            r.register(PermissionDef("a.b", frozenset({ScopeKind.PROJECT}), "y"))

    def test_identical_reregistration_is_fine(self) -> None:
        """모듈이 두 번 import 돼도 터지면 안 된다."""
        r = PermissionRegistry()
        d = PermissionDef("a.b", frozenset({ScopeKind.GLOBAL}), "x")
        r.register(d)
        r.register(d)


class TestHasAndRequire:
    async def test_granted(
        self, service: PermissionService, actor: Actor, project_id: UUID
    ) -> None:
        assert await service.has(
            None,
            actor,
            ISSUE_VIEW,
            scope=Scope.project(project_id),  # type: ignore[arg-type]
        )

    async def test_not_granted(
        self, service: PermissionService, actor: Actor, project_id: UUID
    ) -> None:
        assert not await service.has(
            None,
            actor,
            ISSUE_EDIT,
            scope=Scope.project(project_id),  # type: ignore[arg-type]
        )

    async def test_require_raises_with_permission_in_details(
        self, service: PermissionService, actor: Actor, project_id: UUID
    ) -> None:
        with pytest.raises(PermissionDeniedError) as exc:
            await service.require(
                None,
                actor,
                ISSUE_EDIT,
                scope=Scope.project(project_id),  # type: ignore[arg-type]
            )
        assert exc.value.details["permission"] == ISSUE_EDIT
        assert exc.value.status_code == 403

    async def test_other_project_is_denied(self, service: PermissionService, actor: Actor) -> None:
        """권한은 스코프에 매인다. 다른 프로젝트로 새면 안 된다."""
        assert not await service.has(
            None,
            actor,
            ISSUE_VIEW,
            scope=Scope.project(new_id()),  # type: ignore[arg-type]
        )

    async def test_wrong_scope_kind_is_programmer_error(
        self, service: PermissionService, actor: Actor
    ) -> None:
        """전역 전용 권한을 프로젝트 스코프로 검사하면 조용히 거부가 아니라 예외다."""
        with pytest.raises(ValueError, match="스코프에서 평가할 수 없다"):
            await service.has(
                None,
                actor,
                IDP_MANAGE,
                scope=Scope.project(new_id()),  # type: ignore[arg-type]
            )


class TestActorState:
    async def test_inactive_actor_has_nothing(
        self, service: PermissionService, project_id: UUID
    ) -> None:
        inactive = Actor(user_id=new_id(), email="x@e.com", is_active=False)
        assert not await service.has(
            None,
            inactive,
            ISSUE_VIEW,
            scope=Scope.project(project_id),  # type: ignore[arg-type]
        )

    async def test_customer_cannot_hold_internal_permissions(
        self, service: PermissionService, project_id: UUID
    ) -> None:
        """고객 계정은 내부 권한을 갖지 않는다 (auth.md 5절 고객 격리)."""
        customer = Actor(user_id=new_id(), email="c@e.com", is_customer=True)
        assert not await service.has(
            None,
            customer,
            ISSUE_VIEW,
            scope=Scope.project(project_id),  # type: ignore[arg-type]
        )

    async def test_customer_acl_is_empty(
        self, service: PermissionService, customer_actor: Actor
    ) -> None:
        acl = await service.acl_for(None, customer_actor, ISSUE_VIEW)  # type: ignore[arg-type]
        assert acl.is_empty


class TestRequestScopedCache:
    async def test_resolver_called_once_per_scope(self, actor: Actor, project_id: UUID) -> None:
        """같은 요청 안에서 같은 스코프는 1회만 계산한다."""
        resolver = FakeResolver({Scope.project(project_id): {ISSUE_VIEW, ISSUE_EDIT}})
        service = PermissionService(resolver=resolver)
        scope = Scope.project(project_id)
        for _ in range(5):
            await service.has(None, actor, ISSUE_VIEW, scope=scope)  # type: ignore[arg-type]
            await service.has(None, actor, ISSUE_EDIT, scope=scope)  # type: ignore[arg-type]
        assert resolver.calls == 1

    async def test_cache_is_per_actor(self, project_id: UUID) -> None:
        """캐시가 액터를 넘어가면 권한이 새는 사고가 된다."""
        resolver = FakeResolver({Scope.project(project_id): {ISSUE_VIEW}})
        service = PermissionService(resolver=resolver)
        scope = Scope.project(project_id)
        a = Actor(user_id=new_id(), email="a@e.com")
        b = Actor(user_id=new_id(), email="b@e.com")
        await service.has(None, a, ISSUE_VIEW, scope=scope)  # type: ignore[arg-type]
        await service.has(None, b, ISSUE_VIEW, scope=scope)  # type: ignore[arg-type]
        assert resolver.calls == 2


class TestObjectGuard:
    async def test_guard_can_deny_after_scope_passes(self, actor: Actor, project_id: UUID) -> None:
        """스코프 권한을 통과해도 객체 수준 제한에서 막힐 수 있다."""

        class Doc:
            pass

        class DenyAll:
            async def allows(self, *_args: Any, **_kw: Any) -> bool:
                return False

        resolver = FakeResolver({Scope.project(project_id): {ISSUE_VIEW}})
        service = PermissionService(resolver=resolver)
        service.register_guard(Doc, DenyAll())

        scope = Scope.project(project_id)
        assert await service.has(None, actor, ISSUE_VIEW, scope=scope)  # type: ignore[arg-type]
        assert not await service.has(
            None,
            actor,
            ISSUE_VIEW,
            scope=scope,
            subject=Doc(),  # type: ignore[arg-type]
        )


class TestStepUp:
    async def test_missing_mfa_blocks_sensitive_permission(
        self, service: PermissionService, actor: Actor
    ) -> None:
        with pytest.raises(StepUpRequiredError):
            await service.require(None, actor, IDP_MANAGE, scope=Scope.global_())  # type: ignore[arg-type]

    async def test_stale_mfa_blocks(self, service: PermissionService) -> None:
        stale = Actor(
            user_id=new_id(),
            email="a@e.com",
            mfa_satisfied_at=utcnow() - timedelta(seconds=600),
        )
        with pytest.raises(StepUpRequiredError):
            await service.require(None, stale, IDP_MANAGE, scope=Scope.global_())  # type: ignore[arg-type]

    async def test_recent_mfa_passes_step_up_then_checks_permission(self) -> None:
        """step-up 을 통과해도 권한 자체가 없으면 여전히 거부다."""
        fresh = Actor(user_id=new_id(), email="a@e.com", mfa_satisfied_at=utcnow())
        service = PermissionService(resolver=FakeResolver({}))
        with pytest.raises(PermissionDeniedError):
            await service.require(None, fresh, IDP_MANAGE, scope=Scope.global_())  # type: ignore[arg-type]


class TestAcl:
    def test_global_allows_any_scope(self) -> None:
        acl = Acl(permission=ISSUE_VIEW, is_global=True)
        assert acl.allows(Scope.project(new_id()))
        assert not acl.is_empty

    def test_project_scoped(self, project_id: UUID) -> None:
        acl = Acl(permission=ISSUE_VIEW, project_ids=frozenset({project_id}))
        assert acl.allows(Scope.project(project_id))
        assert not acl.allows(Scope.project(new_id()))
        assert not acl.allows(Scope.global_())

    def test_empty_acl_short_circuits_query(self) -> None:
        assert Acl(permission=ISSUE_VIEW).is_empty
