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
    Text,
    and_,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from ieum.core.permissions import Acl
from ieum.modules.issues.iql import errors, history
from ieum.modules.issues.iql.ast import (
    And,
    Comparison,
    Emptiness,
    EmptinessCheck,
    FieldRef,
    FunctionCall,
    HistoryChanged,
    HistoryWas,
    ListValue,
    Literal,
    Node,
    Not,
    Operator,
    Or,
    Query,
    SortDirection,
    Span,
    TimeWindow,
    Value,
)
from ieum.modules.issues.iql.history import HistorySpec, WindowKind
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
    IssueHistory,
    IssueLabel,
    IssueType,
    Sprint,
    WorkflowState,
    Worklog,
)

#: 결과 상한. 내보내기는 별도 경로를 쓴다 (query-language.md 3절).
MAX_RESULTS = 1000


def _window_conditions(window: tuple[Any, Any] | None) -> list[ColumnElement[bool]]:
    """창을 `created_at` 조건으로.

    양 끝을 **포함**한다. 하루 단위 날짜로 창을 적는 사람이 대부분이고,
    `DURING (1일, 31일)` 이 31일을 빼면 그건 놀라운 일이다.
    """
    if window is None:
        return []
    start, end = window
    out: list[ColumnElement[bool]] = []
    if start is not None:
        out.append(IssueHistory.created_at >= start)
    if end is not None:
        out.append(IssueHistory.created_at <= end)
    return out


def _flatten_values(values: list[Value]) -> list[Literal | FunctionCall]:
    out: list[Literal | FunctionCall] = []
    for value in values:
        out.extend(_flatten(value))
    return out


def _single(value: Value) -> Literal | FunctionCall:
    """값 하나를 꺼낸다. 문법이 목록을 허용하지 않는 자리에서만 쓴다."""
    found = _flatten(value)
    return found[0]


_PARENT = aliased(Issue, name="parent_issue")

#: 묶기 전용 라벨 별칭. 조건의 EXISTS 와 같은 테이블을 쓰면 상관이 꼬인다.
GROUPED_LABEL = aliased(IssueLabel, name="grouped_label")

#: 필드가 요구하는 조인. 컴파일러가 모아서 한 번씩만 건다.
#: project 는 조인하지 않는다 — 키를 미리 ID 로 해석해 Issue.project_id 로
#: 거른다. 모듈 경계(절대규칙 1)를 지키면서 인덱스도 그대로 탄다.
_JOINS: dict[str, Any] = {
    "type": (IssueType, Issue.type_id == IssueType.id),
    "status": (WorkflowState, Issue.state_id == WorkflowState.id),
    "parent": (_PARENT, Issue.parent_id == _PARENT.id),
    # **바깥 조인이다.** 안쪽으로 걸면 백로그 이슈가 통째로 사라지고,
    # `sprint != "..."` 가 "다른 스프린트에 있는 것" 만 뜻하게 된다 —
    # 사람이 기대하는 것은 "그 스프린트가 아닌 것 전부" 다.
    "sprint": (Sprint, Issue.sprint_id == Sprint.id),
    # 라벨은 조건에서는 EXISTS 로 다루지만(이슈 하나가 여럿을 단다), 세는
    # 질의에서는 **묶을 열**이 필요해서 조인이 있어야 한다.
    #
    # **별칭을 쓴다.** 같은 테이블을 조건의 EXISTS 와 묶는 조인이 함께 쓰면,
    # EXISTS 안의 `IssueLabel` 이 바깥 조인으로 자동 상관되어 FROM 절을
    # 잃는다(SQLAlchemy 가 그렇다고 말해 준다). 그러면 `labels = "x"` 로
    # 걸러 놓고 라벨로 묶는 리포트가 통째로 터진다.
    "labels": (GROUPED_LABEL, GROUPED_LABEL.issue_id == Issue.id),
}

#: 바깥으로 걸어야 하는 조인. 안쪽으로 걸면 그 값이 없는 이슈가 통째로
#: 사라진다 — 조건에서는 결과가 줄고, 세는 질의에서는 **총계가 안 맞는다.**
_OUTER_JOINS = frozenset({"sprint", "labels"})


@dataclass(slots=True)
class CompiledQuery:
    where: ColumnElement[bool] | None
    order_by: list[Any] = dataclass_field(default_factory=list)
    joins: list[str] = dataclass_field(default_factory=list)

    def apply(self, stmt: Select[Any]) -> Select[Any]:
        """조인·조건·정렬을 순서대로 얹는다."""
        for name in self.joins:
            target, onclause = _JOINS[name]
            stmt = (
                stmt.outerjoin(target, onclause)
                if name in _OUTER_JOINS
                else stmt.join(target, onclause)
            )
        if self.where is not None:
            stmt = stmt.where(self.where)
        if self.order_by:
            stmt = stmt.order_by(*self.order_by)
        # 커서 페이지네이션의 tie-breaker. UUIDv7 이라 시간순이기도 하다.
        return stmt.order_by(Issue.id)

    def apply_for_aggregate(
        self, stmt: Select[Any], *, extra_joins: tuple[str, ...] = ()
    ) -> Select[Any]:
        """세는 질의에 얹는다. **정렬을 붙이지 않는다.**

        `apply` 는 커서를 위해 `ORDER BY issue.id` 를 붙이는데, 묶는 질의
        에서는 그게 "묶지 않은 열로 정렬한다" 가 되어 Postgres 가 거절한다.

        `extra_joins` 는 묶을 열을 얻기 위해 더 필요한 조인이다. **이미 건
        것과 겹치면 한 번만 건다** — 같은 테이블을 두 번 조인하면 행이
        곱해지고, 그러면 세는 값이 조용히 커진다.
        """
        for name in dict.fromkeys([*self.joins, *extra_joins]):
            target, onclause = _JOINS[name]
            stmt = (
                stmt.outerjoin(target, onclause)
                if name in _OUTER_JOINS
                else stmt.join(target, onclause)
            )
        if self.where is not None:
            stmt = stmt.where(self.where)
        return stmt


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
            case HistoryWas():
                return self._history_was(node)
            case HistoryChanged():
                return self._history_changed(node)
            case _:  # pragma: no cover
                raise errors.syntax_error("해석할 수 없는 조건", offset=0)

    # ── 이력 (M5) ───────────────────────────────────────────────

    def _history_field(self, ref: FieldRef, op_span: Span | None) -> tuple[FieldSpec, HistorySpec]:
        """이력을 물을 수 있는 필드인가.

        **못 물으면 조용히 빈 결과를 주지 않는다.** 이력에 UUID 만 남는
        필드에 이름으로 물으면 절대 안 맞는데, 그건 "그런 이력이 없다" 와
        똑같이 생겼다 — 그래서 여기서 거절한다.
        """
        if ref.is_custom:
            # `changes` 는 커스텀 필드를 `cf.<key>` 로 적으므로 찾을 수는
            # 있다. 그런데 값의 모양이 필드 종류마다 달라서(다중 선택은
            # 배열, 사용자는 UUID) 한 규칙으로 비교할 수 없다 — "언젠가"
            # 가 아니라 **모양을 정한 뒤에** 열 일이다.
            span = op_span or ref.span or Span(0, 1)
            raise errors.invalid_value(
                ref.label(),
                "커스텀 필드에는 이력 연산자를 쓸 수 없다",
                offset=span.offset,
                length=span.length,
            )
        spec = self._spec(ref)
        found = history.spec_for(spec.name)
        if found is None:
            span = op_span or ref.span or Span(0, 1)
            raise errors.invalid_value(
                ref.label(),
                "이력을 물을 수 있는 필드는 " + ", ".join(sorted(history.HISTORY_FIELDS)) + " 다",
                offset=span.offset,
                length=span.length,
            )
        return spec, found

    def _history_was(self, node: HistoryWas) -> ColumnElement[bool]:
        """`field WAS x`.

        **지금 값도 답에 넣는다.** 만들 때부터 그 값이었던 이슈에는 그렇다고
        적힌 이력 줄이 없다 — 이력만 보면 그 이슈들이 통째로 빠진다.

        창이 있으면 "그 창 동안 그 값을 갖고 있었나" 다. 두 가지로 갈린다:
        창 안에서 그 값이 **된** 적이 있거나, 창이 시작될 때 이미 그 값이었거나.
        """
        spec, mapped = self._history_field(node.field, node.operator_span)
        wanted = [
            history.stored_value(self._literal_value(v, node.field, spec.type))
            for v in _flatten_values(node.values)
        ]
        window = self._window(node.window)

        parts: list[ColumnElement[bool]] = []
        for value in wanted:
            if window is None:
                # 창이 없으면 "언제든 그 값이었나" 다. `from` 도 센다 — 그
                # 값에서 벗어난 변경이 그 값이었음을 증언한다.
                parts.append(
                    self._history_exists(mapped, from_value=value, window=None)
                    | self._history_exists(mapped, to_value=value, window=None)
                    | (self._current_text(spec) == literal(value))
                )
            else:
                parts.append(
                    self._history_exists(mapped, to_value=value, window=window)
                    | (self._value_at(mapped, spec, window[0]) == literal(value))
                )
        found = or_(*parts)
        return not_(found) if node.negated else found

    def _history_changed(self, node: HistoryChanged) -> ColumnElement[bool]:
        """`field CHANGED [FROM x] [TO y]`.

        `FROM x TO y` 는 **한 변경 안에서** x→y 여야 한다. 따로 일어난 두
        변경을 이어 붙이면 없던 일을 있다고 답한다 — 그래서 두 조건이 같은
        배열 원소에 걸린다.
        """
        spec, mapped = self._history_field(node.field, node.operator_span)
        return self._history_exists(
            mapped,
            from_value=(
                None
                if node.from_value is None
                else history.stored_value(
                    self._literal_value(_single(node.from_value), node.field, spec.type)
                )
            ),
            to_value=(
                None
                if node.to_value is None
                else history.stored_value(
                    self._literal_value(_single(node.to_value), node.field, spec.type)
                )
            ),
            window=self._window(node.window),
        )

    def _window(self, window: TimeWindow | None) -> tuple[Any, Any] | None:
        """창을 `(시작, 끝)` 로. 한쪽만 있으면 그쪽만 채운다."""
        if window is None or window.kind is WindowKind.ANY:
            return None
        start = None if window.start is None else self._instant(window.start)
        end = None if window.end is None else self._instant(window.end)
        if start is not None and end is not None and end < start:
            span = window.span or Span(0, 1)
            raise errors.invalid_value(
                "DURING", "끝이 시작보다 앞선다", offset=span.offset, length=span.length
            )
        return (start, end)

    def _instant(self, value: Value) -> Any:
        """창의 경계 값. 날짜 함수와 날짜 리터럴을 둘 다 받는다."""
        return self._literal_value(_single(value), FieldRef(name="created"), FieldType.DATE)

    def _history_exists(
        self,
        mapped: HistorySpec,
        *,
        from_value: str | None = None,
        to_value: str | None = None,
        window: tuple[Any, Any] | None = None,
    ) -> ColumnElement[bool]:
        """`issue_history.changes` 배열을 펼쳐 한 원소가 조건을 만족하나.

        **한 원소 안에서** 본다. `from` 과 `to` 를 다른 원소에서 찾으면
        따로 일어난 두 변경이 한 번의 x→y 로 둔갑한다.
        """
        element = func.jsonb_array_elements(IssueHistory.changes).table_valued("value").alias("c")
        item = element.c.value
        conditions: list[ColumnElement[bool]] = [
            IssueHistory.issue_id == Issue.id,
            item.op("->>")(literal("field")) == literal(mapped.stored_as),
        ]
        if from_value is not None:
            conditions.append(item.op("->>")(literal("from")) == literal(from_value))
        if to_value is not None:
            conditions.append(item.op("->>")(literal("to")) == literal(to_value))
        conditions.extend(_window_conditions(window))
        return exists(
            select(literal(1)).select_from(IssueHistory).join(element, true()).where(*conditions)
        )

    def _value_at(self, mapped: HistorySpec, spec: FieldSpec, moment: Any) -> Any:
        """그 시각에 이 필드가 갖고 있던 값.

        **그 뒤 첫 변경의 `from`** 이 답이다. 그 뒤로 아무 변경도 없었다면
        지금 값이 그때 값이다. 생성 시점 값은 따로 기록하지 않지만, 첫
        변경의 `from` 이 그것을 증언하므로 되짚을 수 있다.
        """
        if moment is None:
            return self._current_text(spec)
        element = func.jsonb_array_elements(IssueHistory.changes).table_valued("value").alias("c")
        item = element.c.value
        first_after = (
            select(item.op("->>")(literal("from")))
            .select_from(IssueHistory)
            .join(element, true())
            .where(
                IssueHistory.issue_id == Issue.id,
                item.op("->>")(literal("field")) == literal(mapped.stored_as),
                IssueHistory.created_at >= moment,
            )
            .order_by(IssueHistory.created_at)
            .limit(1)
            .scalar_subquery()
        )
        return func.coalesce(first_after, self._current_text(spec))

    def _current_text(self, spec: FieldSpec) -> Any:
        """지금 값을 이력에 적힌 것과 **같은 모양(텍스트)** 으로."""
        match spec.name:
            case "status":
                self._joins.append("status")
                return WorkflowState.name
            case "assignee":
                return func.cast(Issue.assignee_id, Text)
            case "reporter":
                return func.cast(Issue.reporter_id, Text)
            case _:
                return func.cast(_column(spec.name), Text)

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
            case "sprint":
                self._joins.append("sprint")
                return self._sprintish(Sprint.name, node, values)
            case "sprintstate":
                self._joins.append("sprint")
                return self._sprintish(Sprint.state, node, values, lower=True)
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

        if spec.name in ("sprint", "sprintstate"):
            # **컬럼을 보고 판단한다.** 조인한 `Sprint.name` 이 NULL 인지로
            # 보면 바깥 조인 때문에 같은 답이 나오지만, 조인을 하나 더 걸어야
            # 한다 — 백로그를 묻는 데 조인은 필요 없다.
            return (
                Issue.sprint_id.is_(None)
                if node.kind is Emptiness.EMPTY
                else Issue.sprint_id.is_not(None)
            )

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

    def _sprintish(
        self,
        column: SQLColumnExpression[Any],
        node: Comparison,
        values: list[Any],
        *,
        lower: bool = False,
    ) -> ColumnElement[bool]:
        """스프린트 비교. **부정에는 백로그를 포함한다.**

        바깥 조인만으로는 부족하다: SQL 의 세 값 논리에서 `NULL != 'x'` 는
        참이 아니라 NULL 이므로, 스프린트가 없는 이슈가 통째로 빠진다. 그러면
        `sprint != "이번 주"` 가 "다른 스프린트에 있는 것" 만 뜻하게 되는데,
        사람이 기대하는 것은 "그게 아닌 것 전부" 다 — 백로그가 제일 먼저
        떠오르는 답이다. 시험이 이걸 잡았다.
        """
        base = self._apply(column, node, values, lower=lower)
        if node.operator in (Operator.NE, Operator.NOT_IN):
            return or_(base, Issue.sprint_id.is_(None))
        return base

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
