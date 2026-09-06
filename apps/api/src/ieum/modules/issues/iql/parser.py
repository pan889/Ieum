"""IQL 파서. lark(LALR) → AST.

문법은 `grammar.lark` 에 있다. 여기서는 파스 트리를 AST 로 옮기고,
문법 오류를 위치가 있는 IQLError 로 바꾸는 일만 한다.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, cast

from lark import Lark, Token, Tree
from lark.exceptions import LarkError, UnexpectedInput

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
    SortKey,
    Span,
)

GRAMMAR_PATH = Path(__file__).with_name("grammar.lark")
MAX_QUERY_LENGTH = 4000

#: 이력 연산자는 issue_history 스캔이 필요해 비용이 크다. M5 로 미뤘고,
#: 문법에 넣으면 부분 지원처럼 보이므로 파싱 전에 잡아 명확히 거절한다.
_HISTORY_OPERATORS = re.compile(r"\b(WAS|CHANGED)\b", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _lark() -> Lark:
    """파서는 만드는 비용이 있으므로 프로세스당 하나만 만든다."""
    return Lark(
        GRAMMAR_PATH.read_text(encoding="utf-8"),
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=True,
    )


def grammar() -> Lark:
    """파서 객체 자체가 필요한 곳(자동완성)에 넘긴다."""
    return _lark()


def parse(source: str) -> Query:
    """IQL 문자열을 AST 로. 실패하면 위치가 담긴 IQLError."""
    if len(source) > MAX_QUERY_LENGTH:
        raise errors.syntax_error(
            f"질의가 너무 길다 ({MAX_QUERY_LENGTH}자 제한)",
            offset=MAX_QUERY_LENGTH,
            length=1,
        )
    text = source.strip()
    if not text:
        return Query(where=None, order_by=[], source=source)

    history = _HISTORY_OPERATORS.search(text)
    if history is not None:
        raise errors.unsupported(
            f"이력 연산자 {history.group(0).upper()}",
            offset=history.start(),
            length=len(history.group(0)),
            milestone="M5",
        )

    try:
        tree = _lark().parse(text)
    except UnexpectedInput as exc:
        raise errors.syntax_error(
            f"문법 오류: {_describe(exc)}", offset=getattr(exc, "pos_in_stream", 0) or 0
        ) from exc
    except LarkError as exc:  # pragma: no cover - 파서 내부 오류
        raise errors.syntax_error(f"문법 오류: {exc}", offset=0) from exc

    where, order_by = _query(tree)
    return Query(where=where, order_by=order_by, source=source)


def _describe(exc: UnexpectedInput) -> str:
    token = getattr(exc, "token", None)
    if token is not None:
        return f"예상하지 못한 토큰 {str(token)!r}"
    char = getattr(exc, "char", None)
    if char is not None:
        return f"예상하지 못한 문자 {char!r}"
    return "질의를 해석할 수 없다"


def _span(node: Tree[Token] | Token) -> Span:
    start = getattr(node, "start_pos", None)
    if start is None:
        start = getattr(getattr(node, "meta", None), "start_pos", 0) or 0
    end = getattr(node, "end_pos", None)
    if end is None:
        end = getattr(getattr(node, "meta", None), "end_pos", start) or start
    return Span(offset=int(start), length=max(1, int(end) - int(start)))


def _query(tree: Tree[Token]) -> tuple[Node | None, list[SortKey]]:
    where: Node | None = None
    order_by: list[SortKey] = []
    # lark 타입 스텁은 children 에 None 이 없다고 말하지만, 선택적 규칙
    # (`order_clause?`) 은 실제로 None 자리를 만든다. 스텁을 믿고 가드를
    # 지우면 ORDER BY 없는 질의가 전부 깨진다.
    for child in cast("list[Any]", tree.children):
        if child is None:
            continue
        if isinstance(child, Tree) and child.data == "order_clause":
            order_by = _order(child)
        else:
            where = _node(child)
    return where, order_by


def _node(item: Any) -> Node:
    if not isinstance(item, Tree):  # pragma: no cover - 문법상 도달하지 않는다
        raise errors.syntax_error("질의를 해석할 수 없다", offset=0)

    match item.data:
        case "or_chain":
            return _chain(Or, item)
        case "and_chain":
            return _chain(And, item)
        case "negation":
            return Not(_node(next(c for c in item.children if not _is_keyword(c))))
        case "group":
            return _node(item.children[0])
        case "compare" | "compare_custom":
            return _comparison(item)
        case "emptiness_check" | "emptiness_check_custom":
            return _emptiness(item)
        case _:  # pragma: no cover
            raise errors.syntax_error(f"해석할 수 없는 구문: {item.data}", offset=0)


def _chain(kind: type[And] | type[Or], item: Tree[Token]) -> Node:
    """항이 하나뿐이면 감싸지 않는다. Or(And(x)) 같은 껍데기는 컴파일러와
    테스트를 둘 다 읽기 어렵게 만든다."""
    operands = [_node(c) for c in item.children if not _is_keyword(c)]
    return operands[0] if len(operands) == 1 else kind(operands)


def _is_keyword(child: Any) -> bool:
    return isinstance(child, Token) and child.type in {"AND", "OR", "NOT"}


def _field(item: Any) -> FieldRef:
    if isinstance(item, Token):
        return FieldRef(name=str(item).lower(), span=_span(item))
    # custom_field: CF "[" STRING "]" — 첫 자식은 CF 토큰이다
    key_token = next(c for c in item.children if isinstance(c, Token) and c.type == "STRING")
    return FieldRef(name="cf", custom_key=_unquote(str(key_token)), span=_span(item))


def _comparison(item: Tree[Token]) -> Comparison:
    field_node, op_node, value_node = item.children
    return Comparison(
        field=_field(field_node),
        operator=_operator(cast(Tree[Token], op_node)),
        value=_value(value_node),
        span=_span(item),
    )


def _operator(node: Tree[Token]) -> Operator:
    parts = [str(t).lower() for t in node.children if isinstance(t, Token)]
    text = " ".join(parts)
    match text:
        case "in":
            return Operator.IN
        case "not in":
            return Operator.NOT_IN
        case _:
            return Operator(text)


def _emptiness(item: Tree[Token]) -> EmptinessCheck:
    children = list(item.children)
    field_node = children[0]
    negated = any(isinstance(c, Token) and c.type == "NOT_KW" for c in children)
    return EmptinessCheck(
        field=_field(field_node),
        kind=Emptiness.NOT_EMPTY if negated else Emptiness.EMPTY,
        span=_span(item),
    )


def _value(node: Any) -> Literal | ListValue | FunctionCall:
    if isinstance(node, Tree):
        match node.data:
            case "string_value":
                token = node.children[0]
                return Literal(_unquote(str(token)), _span(token))
            case "date_value":
                token = node.children[0]
                return Literal(str(token), _span(token))
            case "number_value":
                token = node.children[0]
                text = str(token)
                value: Any = float(text) if "." in text else int(text)
                return Literal(value, _span(token))
            case "true_value":
                return Literal(True, _span(node.children[0]))
            case "false_value":
                return Literal(False, _span(node.children[0]))
            case "bareword_value":
                token = node.children[0]
                return Literal(str(token), _span(token))
            case "list":
                return ListValue(
                    [cast(Literal | FunctionCall, _value(c)) for c in node.children],
                    _span(node),
                )
            case "function":
                return _function(node)
    # 문법상 FIELD_NAME 이 값 자리에 오면 따옴표 없는 문자열로 읽는다.
    if isinstance(node, Token):
        return Literal(str(node), _span(node))
    raise errors.syntax_error("값을 해석할 수 없다", offset=0)  # pragma: no cover


def _function(node: Tree[Token]) -> FunctionCall:
    name_token = node.children[0]
    args: list[Literal] = []
    for child in node.children[1:]:
        if isinstance(child, Tree) and child.data == "func_args":
            for arg in child.children:
                parsed = _value(arg)
                if not isinstance(parsed, Literal):
                    span = _span(node)
                    raise errors.invalid_value(
                        str(name_token),
                        "함수 인자에는 리터럴만 쓸 수 있다",
                        offset=span.offset,
                        length=span.length,
                    )
                args.append(parsed)
    return FunctionCall(name=str(name_token), args=args, span=_span(node))


def _order(node: Tree[Token]) -> list[SortKey]:
    keys: list[SortKey] = []
    for child in node.children:
        if not isinstance(child, Tree) or child.data != "sort_item":
            continue
        field_node = child.children[0]
        direction = SortDirection.ASC
        for extra in child.children[1:]:
            if isinstance(extra, Tree) and extra.data == "direction":
                token = extra.children[0]
                if str(token).lower() == "desc":
                    direction = SortDirection.DESC
        keys.append(SortKey(field=_field(field_node), direction=direction))
    return keys


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        body = text[1:-1]
        return body.replace("\\" + text[0], text[0]).replace("\\\\", "\\")
    return text
