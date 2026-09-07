"""권한 커널 (docs/architecture/auth.md 5절).

    principal(user 또는 group)
       └─ role_assignment(scope_kind, scope_id)
             └─ role
                  └─ permission_grant(permission)

설계상 중요한 세 가지:

1. **거부 규칙을 만들지 않는다** (D-20). 평가는 스코프 내 권한의 합집합이다.
   거부가 없으면 "왜 안 보이지"를 추적할 때 경로가 하나뿐이다.
2. **core 는 역할 테이블을 소유하지 않는다.** 테이블은 `org` 모듈 소유이고,
   여기서는 `PermissionResolver` 프로토콜로만 접근한다. 그래야 의존 방향이
   `org → core` 한쪽으로 유지된다.
3. **목록은 검사하지 않고 필터링한다.** 항목마다 require() 를 부르면 N번 질의가
   나가므로, 리포지토리에 `acl_for()` 결과를 넘겨 SQL 에서 걸러낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from ieum.core.exceptions import PermissionDeniedError, StepUpRequiredError
from ieum.core.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ieum.core.context import Actor


class ScopeKind(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    SPACE = "space"
    QUEUE = "queue"


@dataclass(frozen=True, slots=True)
class Scope:
    """권한이 평가되는 범위."""

    kind: ScopeKind
    id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.GLOBAL and self.id is not None:
            raise ValueError("global 스코프는 id 를 갖지 않는다.")
        if self.kind is not ScopeKind.GLOBAL and self.id is None:
            raise ValueError(f"{self.kind} 스코프는 id 가 필요하다.")

    @classmethod
    def global_(cls) -> Scope:
        return cls(ScopeKind.GLOBAL)

    @classmethod
    def project(cls, project_id: UUID) -> Scope:
        return cls(ScopeKind.PROJECT, project_id)

    @classmethod
    def space(cls, space_id: UUID) -> Scope:
        return cls(ScopeKind.SPACE, space_id)

    @classmethod
    def queue(cls, queue_id: UUID) -> Scope:
        return cls(ScopeKind.QUEUE, queue_id)


@dataclass(frozen=True, slots=True)
class PermissionDef:
    """권한 상수의 정의. 모듈이 자기 권한을 등록한다."""

    key: str
    scope_kinds: frozenset[ScopeKind]
    description: str
    #: 최근 MFA 재확인이 필요한 민감 권한 (auth.md 3절 step-up)
    requires_step_up: bool = False


class PermissionRegistry:
    """모든 권한 상수의 단일 출처.

    등록되지 않은 권한 문자열로 검사를 시도하면 즉시 실패한다.
    오타 난 권한이 "항상 거부"로 조용히 굴러가는 것을 막는다.
    """

    def __init__(self) -> None:
        self._defs: dict[str, PermissionDef] = {}

    def register(self, definition: PermissionDef) -> PermissionDef:
        existing = self._defs.get(definition.key)
        if existing is not None and existing != definition:
            raise ValueError(f"권한 '{definition.key}' 가 다른 정의로 이미 등록됐다.")
        self._defs[definition.key] = definition
        return definition

    def register_many(self, definitions: list[PermissionDef]) -> None:
        for d in definitions:
            self.register(d)

    def get(self, key: str) -> PermissionDef:
        try:
            return self._defs[key]
        except KeyError as exc:
            raise KeyError(
                f"등록되지 않은 권한: {key!r}. 모듈의 permissions.py 에서 등록한다."
            ) from exc

    def all(self) -> list[PermissionDef]:
        return sorted(self._defs.values(), key=lambda d: d.key)

    def keys(self) -> frozenset[str]:
        return frozenset(self._defs)

    def __contains__(self, key: object) -> bool:
        return key in self._defs


registry = PermissionRegistry()


@dataclass(frozen=True, slots=True)
class Acl:
    """특정 권한에 대해 사용자가 가진 스코프 집합. 목록 질의 필터로 넘긴다."""

    permission: str
    is_global: bool = False
    project_ids: frozenset[UUID] = frozenset()
    space_ids: frozenset[UUID] = frozenset()
    queue_ids: frozenset[UUID] = frozenset()

    @property
    def is_empty(self) -> bool:
        """아무 스코프도 없으면 질의를 아예 보내지 않아도 된다."""
        return not (self.is_global or self.project_ids or self.space_ids or self.queue_ids)

    def allows(self, scope: Scope) -> bool:
        if self.is_global:
            return True
        match scope.kind:
            case ScopeKind.GLOBAL:
                return False
            case ScopeKind.PROJECT:
                return scope.id in self.project_ids
            case ScopeKind.SPACE:
                return scope.id in self.space_ids
            case ScopeKind.QUEUE:
                return scope.id in self.queue_ids


class PermissionResolver(Protocol):
    """역할 할당을 실제로 읽는 쪽. `org` 모듈이 구현한다."""

    async def permissions_in_scope(
        self, session: AsyncSession, actor: Actor, scope: Scope
    ) -> frozenset[str]:
        """해당 스코프에서 액터가 가진 권한 전체 (상위 스코프 상속 포함)."""
        ...

    async def acl_for(self, session: AsyncSession, actor: Actor, permission: str) -> Acl:
        """액터가 해당 권한을 가진 스코프 집합."""
        ...


class ObjectGuard(Protocol):
    """객체 수준 제한 훅.

    스코프 권한을 통과한 뒤 한 번 더 걸리는 관문이다.
    위키 page_restriction, 이슈 security_level, 데스크 고객 격리가 여기 붙는다.
    """

    async def allows(
        self, session: AsyncSession, actor: Actor, permission: str, subject: Any
    ) -> bool: ...


@dataclass
class PermissionService:
    """서비스 레이어가 호출하는 권한 검사기.

    라우터에서 부르지 않는다. 서비스를 재사용할 때 검사가 누락되기 때문이다
    (auth.md 5절 코드 규약).
    """

    resolver: PermissionResolver
    step_up_window_seconds: int = 300
    _guards: dict[type, ObjectGuard] = field(default_factory=dict)

    def register_guard(self, subject_type: type, guard: ObjectGuard) -> None:
        """모듈이 자기 엔티티 타입에 대한 객체 수준 제한을 등록한다."""
        self._guards[subject_type] = guard

    async def has(
        self,
        session: AsyncSession,
        actor: Actor,
        permission: str,
        *,
        scope: Scope | None = None,
        subject: Any = None,
    ) -> bool:
        definition = registry.get(permission)
        target = scope or Scope.global_()

        if target.kind not in definition.scope_kinds:
            raise ValueError(
                f"권한 '{permission}' 은 {target.kind} 스코프에서 평가할 수 없다. "
                f"허용: {sorted(definition.scope_kinds)}"
            )

        # 비활성 계정과 고객 계정은 내부 권한을 갖지 않는다.
        # 고객의 포털 접근은 라우팅 단계에서 따로 처리한다 (auth.md 5절).
        if not actor.is_active or actor.is_customer:
            return False

        # PAT 은 발급 시 고른 스코프 **교집합**만 쓸 수 있다. 사용자 권한이
        # 나중에 늘어나도 토큰이 같이 커지지 않는다.
        if not actor.scope_allows(permission):
            return False

        granted = await actor.cached(
            ("perms", target.kind, target.id),
            lambda: self.resolver.permissions_in_scope(session, actor, target),
        )
        if permission not in granted:
            return False

        if subject is not None:
            guard = self._guards.get(type(subject))
            if guard is not None and not await guard.allows(session, actor, permission, subject):
                return False

        return True

    async def require(
        self,
        session: AsyncSession,
        actor: Actor,
        permission: str,
        *,
        scope: Scope | None = None,
        subject: Any = None,
    ) -> None:
        """권한이 없으면 PermissionDeniedError 를 던진다."""
        definition = registry.get(permission)

        if definition.requires_step_up:
            self._require_step_up(actor)

        if not await self.has(session, actor, permission, scope=scope, subject=subject):
            raise PermissionDeniedError(
                f"권한이 없다: {permission}",
                details={"permission": permission},
            )

    async def acl_for(self, session: AsyncSession, actor: Actor, permission: str) -> Acl:
        """목록 질의용 ACL. 검사 대신 필터링에 쓴다."""
        registry.get(permission)
        if not actor.is_active or actor.is_customer:
            return Acl(permission=permission)
        result = await actor.cached(
            ("acl", permission),
            lambda: self.resolver.acl_for(session, actor, permission),
        )
        assert isinstance(result, Acl)
        return result

    def require_step_up(self, actor: Actor) -> None:
        """권한과 별개로 step-up 만 요구한다.

        같은 엔드포인트인데 방향에 따라 무게가 다른 경우가 있다 — 보호를
        거는 일과 푸는 일처럼. 권한 정의에 붙이면 양쪽이 같아진다.
        """
        self._require_step_up(actor)

    def _require_step_up(self, actor: Actor) -> None:
        """민감 작업은 최근 N분 내 MFA 재확인을 요구한다."""
        if actor.via_api_token:
            # PAT 은 step-up 을 통과할 수 없다. step-up 은 "사람이 방금 MFA 를
            # 다시 통과했다" 는 뜻인데, 오래 사는 토큰은 그걸 증명할 수 없다.
            raise StepUpRequiredError(
                "이 작업은 API 토큰으로 할 수 없다.",
                code="auth.step_up_not_available_for_token",
            )
        satisfied_at = actor.mfa_satisfied_at
        if satisfied_at is None:
            raise StepUpRequiredError()
        age = (utcnow() - satisfied_at).total_seconds()
        if age > self.step_up_window_seconds:
            raise StepUpRequiredError(details={"window_seconds": self.step_up_window_seconds})


# ── 서비스 배선 ─────────────────────────────────────────────────
# core 는 org 를 import 할 수 없다(의존 방향). 그래서 org 가 구현한 리졸버를
# 기동 시점에 여기 꽂아 넣고, 의존성은 이 홀더에서 꺼내 쓴다.

_service: PermissionService | None = None


def set_permission_service(service: PermissionService) -> None:
    global _service
    _service = service


def get_permission_service() -> PermissionService:
    if _service is None:
        raise RuntimeError(
            "PermissionService 가 배선되지 않았다. main.create_app() 을 거쳐야 한다."
        )
    return _service
