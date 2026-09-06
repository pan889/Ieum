"""워크플로우 엔진.

**상태·전이는 데이터, 규칙은 코드다.** 전이 조건(`conditions`)과 후처리
(`post_functions`)는 등록된 이름 + 파라미터 형태로만 저장한다
(module-guide 워크플로우 엔진 규약).

    {"conditions": [{"type": "role_in", "roles": ["developer"]}],
     "post_functions": [{"type": "set_field", "field": "resolved_at", "value": "$now"}]}

사용자 정의 스크립트는 지원하지 않는다. 워크플로우 설정은 프로젝트 관리자가
편집하는 데이터인데, 거기에 실행 가능한 코드를 넣으면 관리자 권한이 곧
서버 코드 실행 권한이 된다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ieum.core.exceptions import ValidationError
from ieum.core.time import utcnow


@dataclass(frozen=True, slots=True)
class TransitionContext:
    """조건 평가와 후처리에 필요한 것만 담는다. ORM 을 넘기지 않는다."""

    actor_id: UUID
    actor_permissions: frozenset[str]
    issue_id: UUID
    project_id: UUID
    assignee_id: UUID | None
    reporter_id: UUID | None
    from_state: str | None
    to_state: str
    to_state_category: str
    #: 이 전이 요청과 함께 들어온 필드 값 (전이 화면의 입력)
    inputs: dict[str, Any]


class Condition(Protocol):
    """전이 가능 여부를 판정한다. 부수효과가 없어야 한다.

    파라미터는 위치 전용이다. 이름으로 부르지 않으므로 구현체가 쓰지 않는
    인자를 `_ctx` 처럼 표시해도 프로토콜에 맞는다.
    """

    def __call__(self, ctx: TransitionContext, params: dict[str, Any], /) -> bool: ...


@dataclass(slots=True)
class FieldChange:
    """후처리가 만들어내는 변경. 서비스가 실제로 적용한다."""

    field: str
    value: Any


class PostFunction(Protocol):
    """전이 후 적용할 변경을 계산한다. 직접 DB 를 건드리지 않는다.

    Condition 과 같은 이유로 파라미터는 위치 전용이다.
    """

    def __call__(self, ctx: TransitionContext, params: dict[str, Any], /) -> list[FieldChange]: ...


class Registry[T]:
    """이름 → 구현. 등록되지 않은 타입은 설정 저장 시점에 거부한다."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._items: dict[str, T] = {}
        self._schemas: dict[str, frozenset[str]] = {}

    def register(
        self, name: str, *, required_params: frozenset[str] = frozenset()
    ) -> Callable[[T], T]:
        def decorator(impl: T) -> T:
            if name in self._items:
                raise ValueError(f"{self._label} '{name}' 가 이미 등록됐다.")
            self._items[name] = impl
            self._schemas[name] = required_params
            return impl

        return decorator

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError as exc:
            raise ValidationError(
                f"등록되지 않은 {self._label}: {name!r}",
                code="issues.unknown_workflow_rule",
                details={"kind": self._label, "name": name, "known": sorted(self._items)},
            ) from exc

    def validate_spec(self, spec: dict[str, Any]) -> None:
        """저장 전 검증. 여기서 막아야 런타임에 조용히 무시되지 않는다."""
        name = spec.get("type")
        if not isinstance(name, str):
            raise ValidationError(
                f"{self._label} 정의에 'type' 이 없다.",
                code="issues.invalid_workflow_rule",
                details={"spec": spec},
            )
        self.get(name)
        missing = sorted(self._schemas[name] - set(spec))
        if missing:
            raise ValidationError(
                f"{self._label} '{name}' 에 필요한 파라미터가 없다: {missing}",
                code="issues.invalid_workflow_rule",
                details={"name": name, "missing": missing},
            )

    def known(self) -> list[str]:
        return sorted(self._items)


conditions: Registry[Condition] = Registry("전이 조건")
post_functions: Registry[PostFunction] = Registry("후처리")


# ── 내장 조건 ───────────────────────────────────────────────────


@conditions.register("permission", required_params=frozenset({"permission"}))
def _permission(ctx: TransitionContext, params: dict[str, Any]) -> bool:
    """특정 권한을 가진 사람만 전이할 수 있다."""
    return str(params["permission"]) in ctx.actor_permissions


@conditions.register("is_assignee")
def _is_assignee(ctx: TransitionContext, _params: dict[str, Any]) -> bool:
    return ctx.assignee_id is not None and ctx.assignee_id == ctx.actor_id


@conditions.register("is_reporter")
def _is_reporter(ctx: TransitionContext, _params: dict[str, Any]) -> bool:
    return ctx.reporter_id is not None and ctx.reporter_id == ctx.actor_id


@conditions.register("assignee_set")
def _assignee_set(ctx: TransitionContext, _params: dict[str, Any]) -> bool:
    """담당자 없이 진행 상태로 넘어가는 것을 막을 때 쓴다."""
    return ctx.assignee_id is not None


@conditions.register("field_required", required_params=frozenset({"field"}))
def _field_required(ctx: TransitionContext, params: dict[str, Any]) -> bool:
    """전이 화면에서 특정 필드를 반드시 채우게 한다."""
    value = ctx.inputs.get(str(params["field"]))
    return value is not None and value != "" and value != []


# ── 내장 후처리 ─────────────────────────────────────────────────

#: 후처리 값에 쓸 수 있는 치환 토큰. 임의 표현식은 허용하지 않는다.
_TOKENS: dict[str, Callable[[TransitionContext], Any]] = {
    "$now": lambda _ctx: utcnow(),
    "$currentUser": lambda ctx: ctx.actor_id,
    "$null": lambda _ctx: None,
}


def _resolve(value: Any, ctx: TransitionContext) -> Any:
    if isinstance(value, str) and value in _TOKENS:
        return _TOKENS[value](ctx)
    return value


@post_functions.register("set_field", required_params=frozenset({"field", "value"}))
def _set_field(ctx: TransitionContext, params: dict[str, Any]) -> list[FieldChange]:
    return [FieldChange(field=str(params["field"]), value=_resolve(params["value"], ctx))]


@post_functions.register("clear_field", required_params=frozenset({"field"}))
def _clear_field(_ctx: TransitionContext, params: dict[str, Any]) -> list[FieldChange]:
    return [FieldChange(field=str(params["field"]), value=None)]


@post_functions.register("assign_to_actor")
def _assign_to_actor(ctx: TransitionContext, _params: dict[str, Any]) -> list[FieldChange]:
    return [FieldChange(field="assignee_id", value=ctx.actor_id)]


@post_functions.register("set_progress", required_params=frozenset({"value"}))
def _set_progress(_ctx: TransitionContext, params: dict[str, Any]) -> list[FieldChange]:
    value = int(params["value"])
    if not 0 <= value <= 100:
        raise ValidationError("진행률은 0~100 이어야 한다.", code="issues.invalid_workflow_rule")
    return [FieldChange(field="progress", value=value)]


# ── 엔진 ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    """DB 의 workflow_transition 한 행을 평가에 필요한 형태로 좁힌 것."""

    id: UUID
    name: str
    from_state_id: UUID | None
    to_state_id: UUID
    conditions: list[dict[str, Any]]
    post_functions: list[dict[str, Any]]


def validate_rules(
    condition_specs: list[dict[str, Any]], post_function_specs: list[dict[str, Any]]
) -> None:
    """워크플로우 저장 시점 검증.

    런타임이 아니라 저장 시점에 막는다. 그래야 "설정은 됐는데 아무 일도
    일어나지 않는" 상태가 생기지 않는다.
    """
    for spec in condition_specs:
        conditions.validate_spec(spec)
    for spec in post_function_specs:
        post_functions.validate_spec(spec)


def evaluate_conditions(spec: TransitionSpec, ctx: TransitionContext) -> list[str]:
    """통과하지 못한 조건의 이름 목록. 비어 있으면 전이 가능하다."""
    failed: list[str] = []
    for raw in spec.conditions:
        name = str(raw.get("type", ""))
        if not conditions.get(name)(ctx, raw):
            failed.append(name)
    return failed


def apply_post_functions(spec: TransitionSpec, ctx: TransitionContext) -> list[FieldChange]:
    """후처리가 요구하는 변경 목록. 실제 적용은 서비스가 한다."""
    changes: list[FieldChange] = []
    for raw in spec.post_functions:
        changes.extend(post_functions.get(str(raw.get("type", "")))(ctx, raw))
    return changes


#: 기본 워크플로우. 시드가 이 정의로 만든다.
DEFAULT_WORKFLOW_STATES: tuple[tuple[str, str, bool], ...] = (
    # (name, category, is_initial)
    ("Open", "todo", True),
    ("In Progress", "in_progress", False),
    ("Resolved", "done", False),
    ("Closed", "done", False),
)

DEFAULT_TRANSITIONS: tuple[tuple[str, str | None, str, list[dict[str, Any]]], ...] = (
    # (name, from_state, to_state, post_functions)
    ("Start progress", "Open", "In Progress", [{"type": "assign_to_actor"}]),
    ("Stop progress", "In Progress", "Open", [{"type": "set_progress", "value": 0}]),
    (
        "Resolve",
        "In Progress",
        "Resolved",
        [
            {"type": "set_field", "field": "resolved_at", "value": "$now"},
            {"type": "set_progress", "value": 100},
        ],
    ),
    ("Close", "Resolved", "Closed", []),
    (
        "Reopen",
        None,  # 어느 상태에서든
        "Open",
        [
            {"type": "set_field", "field": "resolved_at", "value": "$null"},
            {"type": "set_progress", "value": 0},
        ],
    ),
)
