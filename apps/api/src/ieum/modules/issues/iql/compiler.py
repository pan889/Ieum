"""IQL AST → SQLAlchemy 표현식.

보안 규칙 (query-language.md 3절, 예외 없음):
  - 문자열 보간으로 SQL 을 만들지 않는다. 항상 바인딩 파라미터.
  - 필드·정렬 키·함수는 레지스트리 화이트리스트에만 존재한다.
  - 결과에는 항상 ACL 필터가 AND 로 붙는다. 우회 경로를 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from ieum.core.permissions import Acl
from ieum.modules.issues.iql import errors
from ieum.modules.issues.iql.ast import (
    And,
    Comparison,
    Emptiness,
    EmptinessCheck,
    FieldRef,
    FunctionCall,
    ListValue,
    Literal,
    Node,
    Not,
    Operator,
    Or,
    Query,
    SortDirection,
    Span,
)
from ieum.modules.issues.iql.registry import (
    FIELDS,
    FUNCTIONS,
    FieldSpec,
    FieldType,
    FunctionContext,
    coerce_date,
    known_field_names,
    known_function_names,
)
from ieum.modules.issues.models import (
    Issue,
    IssueFieldValue,
    IssueLabel,
    IssueType,
    WorkflowState,
    Worklog,
)

#: 결과 상한. 내보내기는 별도 경로를 쓴다 (query-language.md 3절).
MAX_RESULTS = 1000

_PARENT = aliased(Issue, name="parent_issue")

#: 필드가 요구하는 조인. 컴파일러가 모아서 한 번씩만 건다.
#: project 는 조인하지 않는다 — 키를 미리 ID 로 해석해 Issue.project_id 로
#: 거른다. 모듈 경계(절대규칙 1)를 지키면서 인덱스도 그대로 탄다.
_JOINS: dict[str, Any] = {
    "type": (IssueType, Issue.type_id == IssueType.id),
    "status": (WorkflowState, Issue.state_id == WorkflowState.id),
    "parent": (_PARENT, Issue.parent_id == _PARENT.id),
}


@dataclass(slots=True)
class CompiledQuery:
    where: ColumnElement[bool] | None
    order_by: list[Any] = dataclass_field(default_factory=list)
    joins: list[str] = dataclass_field(default_factory=list)

    def apply(self, stmt: Select[Any]) -> Select[Any]:
        """조인·조건·정렬을 순서대로 얹는다."""
        for name in self.joins:
            target, onclause = _JOINS[name]
            stmt = stmt.join(target, onclause)
        if self.where is not None:
            stmt = stmt.where(self.where)
        if self.order_by:
            stmt = stmt.order_by(*self.order_by)
        # 커서 페이지네이션의 tie-breaker. UUIDv7 이라 시간순이기도 하다.
        return stmt.order_by(Issue.id)


class Compiler:
    def __init__(self, ctx: FunctionContext, *, project_ids: dict[str, UUID] | None = None) -> None:
        self._ctx = ctx
        self._joins: list[str] = []
        #: 프로젝트 키(대문자) → id. 호출자가 org.contracts 로 미리 해석해 넘긴다.
        self._project_ids = project_ids or {}

    def compile(self, query: Query, *, acl: Acl) -> CompiledQuery:
        """AST 를 SQL 조건으로. ACL 은 여기서 무조건 AND 로 붙는다."""
        conditions: list[ColumnElement[bool]] = []
        if query.where is not None:
            conditions.append(self._node(query.where))

        acl_filter = self._acl_filter(acl)
        conditions.append(acl_filter)

        order_by = [self._sort(key) for key in query.order_by]
        return CompiledQuery(
            where=and_(*conditions), order_by=order_by, joins=list(dict.fromkeys(self._joins))
        )

    # ── ACL ─────────────────────────────────────────────────────

    def _acl_filter(self, acl: Acl) -> ColumnElement[bool]:
        if acl.is_global:
            return Issue.archived_at.is_(None) | Issue.archived_at.is_not(None)  # 항상 참
        if acl.is_empty:
            # 볼 수 있는 프로젝트가 없다. 조건을 거짓으로 만들어 결과를 비운다.
            return Issue.id.is_(None)
        return Issue.project_id.in_(acl.project_ids)

    # ── 노드 ────────────────────────────────────────────────────

    def _node(self, node: Node) -> ColumnElement[bool]:
        match node:
            case And(operands):
                return and_(*(self._node(o) for o in operands))
            case Or(operands):
                return or_(*(self._node(o) for o in operands))
            case Not(operand):
                return not_(self._node(operand))
            case Comparison():
                return self._comparison(node)
            case EmptinessCheck():
                return self._emptiness(node)
            case _:  # pragma: no cover
                raise errors.syntax_error("해석할 수 없는 조건", offset=0)

    def _comparison(self, node: Comparison) -> ColumnElement[bool]:
        if node.field.is_custom:
            return self._custom_comparison(node)

        spec = self._spec(node.field)
        self._check_operator(spec, node)
        values = self._values(spec, node)

        match spec.name:
            case "labels":
                return self._labels(node.operator, values)
            case "project":
                return self._project(node, values)
            case "type":
                self._joins.append("type")
                return self._apply(IssueType.name, node, values)
            case "status":
                self._joins.append("status")
                return self._apply(WorkflowState.name, node, values)
            case "statuscategory":
                self._joins.append("status")
                return self._apply(WorkflowState.category, node, values, lower=True)
            case "parent":
                self._joins.append("parent")
                return self._apply(_PARENT.key_seq, node, values)
            case "archived":
                wanted = bool(values[0])
                return Issue.archived_at.is_not(None) if wanted else Issue.archived_at.is_(None)
            case _:
                return self._apply(_column(spec.name), node, values)

    def _project(self, node: Comparison, values: list[Any]) -> ColumnElement[bool]:
        """프로젝트 키를 미리 해석해 둔 id 로 바꾼다.

        조인하지 않으므로 issue 의 project 인덱스를 그대로 탄다. 없는 키는
        빈 결과가 아니라 오류다 — 오타를 조용히 삼키면 "왜 결과가 없지"가 된다.
        """
        resolved: list[UUID] = []
        for raw in values:
            key = str(raw).upper()
            found = self._project_ids.get(key)
            if found is None:
                span = node.value.span or node.field.span or Span(0, 1)
                raise errors.invalid_value(
                    "project",
                    f"프로젝트 {key!r} 를 찾을 수 없다",
                    offset=span.offset,
                    length=span.length,
                    project=key,
                )
            resolved.append(found)

        match node.operator:
            case Operator.EQ | Operator.IN:
                return Issue.project_id.in_(resolved)
            case _:
                return Issue.project_id.not_in(resolved)

    def _custom_comparison(self, node: Comparison) -> ColumnElement[bool]:
        """커스텀 필드는 JSONB 조회다.

        동등 비교는 JSONB 끼리 한다 — GIN 인덱스를 타고, 값이 문자열인지
        숫자인지에 따라 결과가 달라지지 않는다. 텍스트 매칭(~)만 `#>> '{}'`
        로 스칼라를 뽑아 쓴다. `astext` 는 인덱스 접근 결과에만 있으므로
        컬럼 전체에는 쓸 수 없다.
        """
        key = node.field.custom_key or ""
        values = [self._literal_value(v, node.field, FieldType.TEXT) for v in _flatten(node.value)]

        def base() -> Select[Any]:
            return select(IssueFieldValue.issue_id).where(
                IssueFieldValue.issue_id == Issue.id, IssueFieldValue.field_key == key
            )

        as_text = IssueFieldValue.value.op("#>>")(literal("{}"))

        match node.operator:
            case Operator.EQ:
                return exists(base().where(IssueFieldValue.value == _as_jsonb(values[0])))
            case Operator.NE:
                return not_(exists(base().where(IssueFieldValue.value == _as_jsonb(values[0]))))
            case Operator.IN:
                return exists(
                    base().where(IssueFieldValue.value.in_([_as_jsonb(v) for v in values]))
                )
            case Operator.NOT_IN:
                return not_(
                    exists(base().where(IssueFieldValue.value.in_([_as_jsonb(v) for v in values])))
                )
            case Operator.CONTAINS:
                return exists(base().where(as_text.ilike(f"%{values[0]}%")))
            case Operator.NOT_CONTAINS:
                return not_(exists(base().where(as_text.ilike(f"%{values[0]}%"))))
            case _:
                span = node.operator_span or node.field.span or Span(0, 1)
                raise errors.operator_not_allowed(
                    node.field.label(),
                    node.operator.value,
                    ["=", "!=", "in", "not in", "~", "!~"],
                    offset=span.offset,
                    length=span.length,
                )

    def _emptiness(self, node: EmptinessCheck) -> ColumnElement[bool]:
        if node.field.is_custom:
            key = node.field.custom_key or ""
            present = exists(
                select(IssueFieldValue.issue_id).where(
                    IssueFieldValue.issue_id == Issue.id,
                    IssueFieldValue.field_key == key,
                )
            )
            return not_(present) if node.kind is Emptiness.EMPTY else present

        spec = self._spec(node.field)
        if not spec.nullable:
            span = node.field.span or Span(0, 1)
            raise errors.invalid_value(
                spec.name,
                "이 필드는 항상 값이 있어 EMPTY 검사를 쓸 수 없다",
                offset=span.offset,
                length=span.length,
            )
        if spec.name == "labels":
            present = exists(select(IssueLabel.issue_id).where(IssueLabel.issue_id == Issue.id))
            return not_(present) if node.kind is Emptiness.EMPTY else present

        column = _column(spec.name)
        return column.is_(None) if node.kind is Emptiness.EMPTY else column.is_not(None)

    def _labels(self, operator: Operator, values: list[Any]) -> ColumnElement[bool]:
        base = select(IssueLabel.issue_id).where(IssueLabel.issue_id == Issue.id)
        texts = [str(v) for v in values]
        match operator:
            case Operator.EQ | Operator.IN:
                return exists(base.where(IssueLabel.label.in_(texts)))
            case Operator.NE | Operator.NOT_IN:
                return not_(exists(base.where(IssueLabel.label.in_(texts))))
            case Operator.CONTAINS:
                return exists(base.where(IssueLabel.label.ilike(f"%{texts[0]}%")))
            case _:
                return not_(exists(base.where(IssueLabel.label.ilike(f"%{texts[0]}%"))))

    # ── 값 ──────────────────────────────────────────────────────

    def _apply(
        self,
        column: SQLColumnExpression[Any],
        node: Comparison,
        values: list[Any],
        *,
        upper: bool = False,
        lower: bool = False,
    ) -> ColumnElement[bool]:
        prepared = [
            v.upper()
            if upper and isinstance(v, str)
            else v.lower()
            if lower and isinstance(v, str)
            else v
            for v in values
        ]
        match node.operator:
            case Operator.EQ:
                return column == prepared[0]
            case Operator.NE:
                return column != prepared[0]
            case Operator.GT:
                return column > prepared[0]
            case Operator.GTE:
                return column >= prepared[0]
            case Operator.LT:
                return column < prepared[0]
            case Operator.LTE:
                return column <= prepared[0]
            case Operator.IN:
                return column.in_(prepared)
            case Operator.NOT_IN:
                return column.not_in(prepared)
            case Operator.CONTAINS:
                return column.ilike(f"%{prepared[0]}%")
            case Operator.NOT_CONTAINS:
                return not_(column.ilike(f"%{prepared[0]}%"))

    def _values(self, spec: FieldSpec, node: Comparison) -> list[Any]:
        raw = _flatten(node.value)
        if node.operator in {Operator.IN, Operator.NOT_IN}:
            if not isinstance(node.value, ListValue):
                span = node.value.span or Span(0, 1)
                raise errors.invalid_value(
                    spec.name,
                    f"{node.operator.value} 에는 괄호로 묶은 목록이 필요하다",
                    offset=span.offset,
                    length=span.length,
                )
        elif len(raw) != 1:
            span = node.value.span or Span(0, 1)
            raise errors.invalid_value(
                spec.name, "값이 하나여야 한다", offset=span.offset, length=span.length
            )
        return [self._literal_value(v, node.field, spec.type) for v in raw]

    def _literal_value(
        self, value: Literal | FunctionCall, ref: FieldRef, expected: FieldType
    ) -> Any:
        if isinstance(value, FunctionCall):
            return self._call(value, expected)
        return self._coerce(value, ref, expected)

    def _coerce(self, literal: Literal, ref: FieldRef, expected: FieldType) -> Any:
        span = literal.span or ref.span or Span(0, 1)
        raw = literal.value
        match expected:
            case FieldType.NUMBER:
                if isinstance(raw, bool) or not isinstance(raw, int | float):
                    raise errors.invalid_value(
                        ref.label(), "숫자여야 한다", offset=span.offset, length=span.length
                    )
                return raw
            case FieldType.DATE:
                parsed = coerce_date(raw)
                if parsed is None:
                    raise errors.invalid_value(
                        ref.label(),
                        "날짜여야 한다 (YYYY-MM-DD 또는 날짜 함수)",
                        offset=span.offset,
                        length=span.length,
                    )
                return parsed
            case FieldType.USER:
                if isinstance(raw, UUID):
                    return raw
                try:
                    return UUID(str(raw))
                except ValueError as exc:
                    raise errors.invalid_value(
                        ref.label(),
                        "사용자 ID 또는 currentUser() 여야 한다",
                        offset=span.offset,
                        length=span.length,
                    ) from exc
            case FieldType.BOOL:
                if isinstance(raw, bool):
                    return raw
                if str(raw).lower() in {"true", "false"}:
                    return str(raw).lower() == "true"
                raise errors.invalid_value(
                    ref.label(), "true/false 여야 한다", offset=span.offset, length=span.length
                )
            case _:
                return raw

    def _call(self, call: FunctionCall, expected: FieldType) -> Any:
        span = call.span or Span(0, 1)
        spec = FUNCTIONS.get(call.name.lower())
        if spec is None:
            raise errors.unknown_function(
                call.name, known_function_names(), offset=span.offset, length=span.length
            )
        low, high = spec.arity
        if not low <= len(call.args) <= high:
            raise errors.invalid_value(
                spec.name,
                f"인자는 {low}~{high}개여야 한다 ({len(call.args)}개 받음)",
                offset=span.offset,
                length=span.length,
            )
        if spec.returns is not expected and expected is not FieldType.TEXT:
            raise errors.invalid_value(
                spec.name,
                f"{spec.returns} 를 돌려주므로 {expected} 필드에 쓸 수 없다",
                offset=span.offset,
                length=span.length,
            )
        return spec.evaluate(self._ctx, [a.value for a in call.args])

    # ── 정렬 ────────────────────────────────────────────────────

    def _sort(self, key: Any) -> Any:
        ref: FieldRef = key.field
        if ref.is_custom:
            span = ref.span or Span(0, 1)
            raise errors.unsupported(
                "커스텀 필드 정렬",
                offset=span.offset,
                length=span.length,
                milestone="M5",
            )
        spec = self._spec(ref)
        if not spec.sortable:
            span = ref.span or Span(0, 1)
            raise errors.invalid_value(
                spec.name, "정렬 기준으로 쓸 수 없다", offset=span.offset, length=span.length
            )
        column = self._sort_column(spec)
        return column.desc() if key.direction is SortDirection.DESC else column.asc()

    def _sort_column(self, spec: FieldSpec) -> Any:
        match spec.name:
            case "type":
                self._joins.append("type")
                return IssueType.name
            case "status":
                self._joins.append("status")
                return WorkflowState.position
            case "statuscategory":
                self._joins.append("status")
                return WorkflowState.category
            case "parent":
                self._joins.append("parent")
                return _PARENT.key_seq
            case _:
                return _column(spec.name)

    # ── 공통 ────────────────────────────────────────────────────

    def _spec(self, ref: FieldRef) -> FieldSpec:
        spec = FIELDS.get(ref.name.lower())
        if spec is None:
            span = ref.span or Span(0, 1)
            raise errors.unknown_field(
                ref.name, known_field_names(), offset=span.offset, length=span.length
            )
        return spec

    def _check_operator(self, spec: FieldSpec, node: Comparison) -> None:
        if spec.allows(node.operator):
            return
        # 연산자 자리를 가리킨다. 조건 전체를 가리키면 "이 줄 어딘가" 로만
        # 읽혀서, 정작 고쳐야 할 글자를 여전히 안 알려준다.
        span = node.operator_span or node.span or Span(0, 1)
        raise errors.operator_not_allowed(
            spec.name,
            node.operator.value,
            spec.allowed_operators(),
            offset=span.offset,
            length=span.length,
        )


#: 필드 이름 → Issue 컬럼. 화이트리스트이므로 getattr 로 임의 접근하지 않는다.
def _spent_minutes() -> SQLColumnExpression[Any]:
    """이슈별 worklog 합계. 기록이 없으면 0 이다 (NULL 이 아니라)."""
    return (
        select(func.coalesce(func.sum(Worklog.spent_minutes), 0))
        .where(Worklog.issue_id == Issue.id)
        .correlate(Issue)
        .scalar_subquery()
    )


_COLUMNS: dict[str, SQLColumnExpression[Any]] = {
    "key": Issue.key_seq,
    "summary": Issue.summary,
    "description": Issue.description,
    "priority": Issue.priority,
    "assignee": Issue.assignee_id,
    "reporter": Issue.reporter_id,
    "created": Issue.created_at,
    "updated": Issue.updated_at,
    "due": Issue.due_date,
    "startdate": Issue.start_date,
    "resolved": Issue.resolved_at,
    "estimate": Issue.estimate_minutes,
    "timespent": _spent_minutes(),
    "progress": Issue.progress,
}


def _column(name: str) -> SQLColumnExpression[Any]:
    return _COLUMNS[name]


def _as_jsonb(value: Any) -> Any:
    """파이썬 스칼라를 JSONB 리터럴로. 바인딩 파라미터로 나간다."""
    return literal(value, JSONB)


def _flatten(value: Any) -> list[Literal | FunctionCall]:
    if isinstance(value, ListValue):
        return list(value.items)
    return [value]


def project_keys_in(query: Query) -> set[str]:
    """질의가 참조하는 프로젝트 키. 호출자가 org.contracts 로 해석한다."""
    keys: set[str] = set()

    def walk(node: Node | None) -> None:
        match node:
            case And(operands) | Or(operands):
                for child in operands:
                    walk(child)
            case Not(operand):
                walk(operand)
            case Comparison() if not node.field.is_custom and node.field.name == "project":
                for item in _flatten(node.value):
                    if isinstance(item, Literal):
                        keys.add(str(item.value).upper())
            case _:
                return

    walk(query.where)
    return keys


def compile_query(
    query: Query,
    *,
    acl: Acl,
    ctx: FunctionContext,
    project_ids: dict[str, UUID] | None = None,
) -> CompiledQuery:
    return Compiler(ctx, project_ids=project_ids).compile(query, acl=acl)


__all__ = [
    "MAX_RESULTS",
    "CompiledQuery",
    "Compiler",
    "compile_query",
    "project_keys_in",
]
