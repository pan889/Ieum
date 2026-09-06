"""IQL 자동완성 — 커서가 놓인 자리에서 무엇이 올 수 있는지 정한다.

**후보를 손으로 나열하지 않는다.** 문법은 `grammar.lark` 한 곳에만 있으므로
"이 자리에 올 수 있는 것" 은 lark 의 대화형 파서에게 묻는다. 문법이 자라면
제안도 따라 자란다 — 규칙을 옮겨 적어 두면 반드시 어긋난다(D-77 과 같은 이유:
클라이언트에 파서를 또 만들지 않는 것과 한 몸이다).

DB 를 건드리지 않는 부분만 여기 둔다. 프로젝트 키·상태 이름·사용자 같은 값
후보는 권한을 타므로 `SearchService.suggest` 가 채운다.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from lark import Token
from lark.exceptions import LarkError

from ieum.modules.issues.iql.ast import Operator
from ieum.modules.issues.iql.parser import MAX_QUERY_LENGTH, grammar
from ieum.modules.issues.iql.registry import FIELDS, FUNCTIONS, FieldSpec, FieldType

#: 커서 앞에서 이어 치던 낱말. 필드명·키워드·bareword 가 모두 여기 걸린다.
_WORD_TAIL = re.compile(r"[A-Za-z0-9_.\-]+$")
#: `!` 처럼 아직 완성되지 않은 연산자.
_OP_TAIL = re.compile(r"[=!<>~]+$")

#: 값이 올 수 있는 자리인지 판단하는 터미널.
_VALUE_TERMINALS = frozenset({"STRING", "BAREWORD", "NUMBER", "DATE", "TRUE", "FALSE"})

#: 기호가 아니라 낱말인 후보. 화면에 보일 글자를 여기서 정한다.
_KEYWORDS: dict[str, str] = {
    "AND": "AND",
    "OR": "OR",
    "NOT": "NOT",
    "ORDER": "ORDER BY",
    "ASC": "ASC",
    "DESC": "DESC",
    "EMPTY": "EMPTY",
    "NULL": "NULL",
}


class SuggestKind(StrEnum):
    FIELD = "field"
    OPERATOR = "operator"
    FUNCTION = "function"
    VALUE = "value"
    KEYWORD = "keyword"


#: 같은 자리에 여러 종류가 겹칠 때의 순서. 값 자리에서는 값이 먼저 보여야 한다.
_ORDER: dict[SuggestKind, int] = {
    SuggestKind.VALUE: 0,
    SuggestKind.FUNCTION: 1,
    SuggestKind.FIELD: 2,
    SuggestKind.OPERATOR: 3,
    SuggestKind.KEYWORD: 4,
}


@dataclass(frozen=True, slots=True)
class Suggestion:
    #: 목록에 보이는 글자. 사용자가 이걸 보고 고른다.
    label: str
    #: 실제로 끼워 넣을 글자. 사용자 ID 처럼 label 과 다를 수 있다.
    insert: str
    kind: SuggestKind
    #: 오른쪽에 흐리게 붙는 설명. 없으면 빈 문자열.
    detail: str = ""
    #: insert 안에서 커서가 멈출 자리. None 이면 끝.
    caret: int | None = None
    #: 같은 종류 안에서 뒤로 미룰 무게. 커스텀 필드는 기본 필드 다음이다.
    weight: int = 0

    def caret_at(self) -> int:
        return len(self.insert) if self.caret is None else self.caret


@dataclass(frozen=True, slots=True)
class Context:
    """커서가 놓인 자리."""

    #: 사용자가 이미 친 조각. 후보를 거르는 데 쓴다.
    prefix: str
    #: 갈아 끼울 범위 [start, end). end 는 닫는 따옴표를 삼키느라 커서보다 뒤일 수 있다.
    start: int
    end: int
    #: 문자열 리터럴 안인가. 그렇다면 따옴표를 다시 넣지 않는다.
    quote: str
    #: 문법이 이 자리에 허용하는 터미널.
    accepts: frozenset[str] = frozenset()
    #: 이 조건이 다루는 필드. 연산자·값을 이 필드에 맞춰 좁힌다.
    spec: FieldSpec | None = None
    #: `cf["key"]` 의 key. 커스텀 필드면 spec 대신 이쪽이 찬다.
    custom_key: str | None = None
    #: ORDER BY 뒤인가. 그렇다면 정렬 가능한 필드만 제안한다.
    in_order_by: bool = False
    #: 값 자리인가.
    wants_value: bool = False
    #: 바로 앞이 `IN` 인가. 목록은 괄호부터 열어야 한다.
    after_in: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start


@functools.lru_cache(maxsize=1)
def _symbols() -> dict[str, str]:
    """터미널 이름 → 기호.

    `!=` 같은 익명 터미널의 이름(`__ANON_3`)은 문법에 적힌 순서에 딸린 것이라
    손으로 적어 두면 문법을 고칠 때 조용히 어긋난다. 문법에서 읽어 온다.
    """
    return {
        term.name: term.pattern.value for term in grammar().terminals if term.pattern.type == "str"
    }


#: 기호 → 연산자. 필드가 허용하는 연산자만 남기는 데 쓴다.
_OPERATOR_BY_TEXT: dict[str, Operator] = {op.value: op for op in Operator}

#: 연산자를 보여 줄 순서. 알파벳순으로 두면 `!=` 가 `=` 보다 앞에 서서,
#: 아무 생각 없이 Enter 를 친 사람이 정반대 조건을 얻는다.
_OPERATOR_ORDER: tuple[str, ...] = (
    "=",
    "!=",
    "~",
    "!~",
    ">",
    ">=",
    "<",
    "<=",
    "IN",
    "NOT IN",
    "IS EMPTY",
    "IS NOT EMPTY",
)


def _operator_weight(label: str) -> int:
    return _OPERATOR_ORDER.index(label) if label in _OPERATOR_ORDER else len(_OPERATOR_ORDER)


def _open_quote(text: str) -> int | None:
    """닫히지 않은 따옴표의 위치. 없으면 None."""
    opened: int | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if opened is None:
            if char in "\"'":
                opened = i
        elif char == "\\":
            i += 1
        elif char == text[opened]:
            opened = None
        i += 1
    return opened


def _split(head: str) -> tuple[int, int, str]:
    """`(파서에 먹일 지점, 갈아 끼울 시작, 열린 따옴표)`.

    커서 바로 앞 토큰은 아직 완성되지 않았다. 그대로 파서에 먹이면 `pro` 가
    끝난 필드명으로 읽혀 "다음은 연산자" 라고 답한다 — 정작 필요한 건 `pro`
    로 시작하는 필드 목록이다. 그래서 잘라 내고 접두사로 쓴다.
    """
    quote_at = _open_quote(head)
    if quote_at is not None:
        # 따옴표째 갈아 끼운다. 안쪽만 바꾸면 `quote_value` 가 붙인 따옴표와
        # 이미 친 따옴표가 겹쳐 `""ENG"` 가 된다.
        return quote_at, quote_at, head[quote_at]
    for pattern in (_WORD_TAIL, _OP_TAIL):
        found = pattern.search(head)
        if found is not None:
            return found.start(), found.start(), ""
    return len(head), len(head), ""


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1].replace("\\" + raw[0], raw[0]).replace("\\\\", "\\")
    return raw


def _condition_field(tokens: list[Token]) -> tuple[FieldSpec | None, str | None]:
    """이 조건이 다루는 필드. 뒤에서부터 찾는다 — 값은 늘 제 필드를 뒤따른다."""
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if token.type == "FIELD_NAME":
            return FIELDS.get(str(token).lower()), None
        if token.type == "CF":
            after = tokens[index + 1 : index + 4]
            key = next((_unquote(str(t)) for t in after if t.type == "STRING"), None)
            return None, key
    return None, None


def analyze(source: str, offset: int) -> Context | None:
    """커서 위치를 문법에 비춰 해석한다. 해석할 수 없으면 None."""
    if len(source) > MAX_QUERY_LENGTH:
        return None
    offset = max(0, min(offset, len(source)))
    head_text = source[:offset]
    feed_to, start, quote = _split(head_text)
    prefix = head_text[start + len(quote) :]

    try:
        interactive = grammar().parse_interactive(head_text[:feed_to])
        tokens = interactive.exhaust_lexer()
        # lark 스텁이 accepts() 를 타입 없이 내놓는다.
        accepts = frozenset(cast("set[str]", interactive.accepts()))  # type: ignore[no-untyped-call]
    except LarkError:
        # 앞이 이미 깨져 있으면 제안할 자리를 알 수 없다. 조용히 아무것도
        # 내놓지 않는다 — 틀린 제안은 없는 것만 못하다.
        return None

    # 열어 둔 따옴표를 닫아 주려면 이미 있는 닫는 따옴표를 삼켜야 한다.
    end = offset
    if quote and source[offset : offset + 1] == quote:
        end = offset + 1

    spec, custom_key = _condition_field(tokens)
    return Context(
        prefix=prefix,
        start=start,
        end=end,
        quote=quote,
        accepts=accepts,
        spec=spec,
        custom_key=custom_key,
        in_order_by=any(token.type == "BY" for token in tokens),
        wants_value=bool(accepts & _VALUE_TERMINALS),
        after_in=bool(tokens) and tokens[-1].type == "IN",
    )


# ── 후보 ────────────────────────────────────────────────────────


def quote_value(ctx: Context, value: str) -> str:
    """값을 IQL 리터럴로. 따옴표 안이면 이미 열린 따옴표를 재사용한다."""
    mark = ctx.quote or '"'
    escaped = value.replace("\\", "\\\\").replace(mark, "\\" + mark)
    return f"{mark}{escaped}{mark}"


def _fields(ctx: Context) -> list[Suggestion]:
    if "FIELD_NAME" not in ctx.accepts:
        return []
    seen: dict[str, FieldSpec] = {}
    for spec in FIELDS.values():
        if ctx.in_order_by and not spec.sortable:
            continue
        seen.setdefault(spec.name, spec)
    return [
        Suggestion(
            label=spec.name,
            insert=f"{spec.name} ",
            kind=SuggestKind.FIELD,
            detail=spec.description,
        )
        for spec in (seen[name] for name in sorted(seen))
    ]


def _operators(ctx: Context) -> list[Suggestion]:
    out: list[Suggestion] = []
    for name, text in _symbols().items():
        if name not in ctx.accepts:
            continue
        operator = _OPERATOR_BY_TEXT.get(text)
        if operator is None:
            # `(`, `,` 처럼 연산자가 아닌 기호. 값 자리에서 따로 다룬다.
            continue
        if ctx.spec is not None and not ctx.spec.allows(operator):
            continue
        out.append(
            Suggestion(
                label=text,
                insert=f"{text} ",
                kind=SuggestKind.OPERATOR,
                weight=_operator_weight(text),
            )
        )

    if "IN" in ctx.accepts and (ctx.spec is None or ctx.spec.allows(Operator.IN)):
        out.append(
            Suggestion(
                label="IN", insert="IN (", kind=SuggestKind.OPERATOR, weight=_operator_weight("IN")
            )
        )
    # `IS NOT EMPTY` 의 NOT 도 같은 터미널이다. IN 이 함께 허용될 때만
    # 목록 부정이다 — 아니면 `assignee IS ` 자리에 `NOT IN` 이 뜬다.
    if {"NOT_KW", "IN"} <= ctx.accepts and (ctx.spec is None or ctx.spec.allows(Operator.NOT_IN)):
        out.append(
            Suggestion(
                label="NOT IN",
                insert="NOT IN (",
                kind=SuggestKind.OPERATOR,
                weight=_operator_weight("NOT IN"),
            )
        )
    if "IS" in ctx.accepts and (ctx.spec is None or ctx.spec.nullable):
        # 빈 값 검사는 두 걸음(`IS` → `EMPTY`)이 늘 붙어 다닌다. 한 번에 준다.
        out.append(
            Suggestion(
                label="IS EMPTY",
                insert="IS EMPTY ",
                kind=SuggestKind.OPERATOR,
                weight=_operator_weight("IS EMPTY"),
            )
        )
        out.append(
            Suggestion(
                label="IS NOT EMPTY",
                insert="IS NOT EMPTY ",
                kind=SuggestKind.OPERATOR,
                weight=_operator_weight("IS NOT EMPTY"),
            )
        )
    return out


def _functions(ctx: Context) -> list[Suggestion]:
    # 어느 필드의 값인지 모르면(커스텀 필드·오타) 함수를 고르지 않는다.
    # 컴파일러는 통과시켜 줄지 몰라도 `summary = now()` 는 사람이 원한 게
    # 아니다. 확실할 때만 제안한다.
    if "FUNC_NAME" not in ctx.accepts or ctx.spec is None:
        return []
    expected = ctx.spec.type
    out: list[Suggestion] = []
    for name in sorted({spec.name for spec in FUNCTIONS.values()}):
        spec = FUNCTIONS[name.lower()]
        if spec.returns is not expected:
            continue
        insert = f"{name}()"
        # 인자를 받는 함수는 괄호 안에서 멈춘다.
        caret = len(insert) - 1 if spec.arity[1] > 0 else len(insert)
        out.append(
            Suggestion(
                label=f"{name}()",
                insert=insert,
                kind=SuggestKind.FUNCTION,
                detail=spec.description,
                caret=caret,
            )
        )
    return out


def _keywords(ctx: Context) -> list[Suggestion]:
    out: list[Suggestion] = []
    for name, text in _KEYWORDS.items():
        if name not in ctx.accepts:
            continue
        out.append(Suggestion(label=text, insert=f"{text} ", kind=SuggestKind.KEYWORD))
    if "NOT_KW" in ctx.accepts and "IN" not in ctx.accepts:
        out.append(Suggestion(label="NOT", insert="NOT ", kind=SuggestKind.KEYWORD))
    if ctx.after_in and "LPAR" in ctx.accepts:
        # 목록은 괄호가 먼저다. 여기서 값을 끼우면 `x IN "A"` 가 되어 거절당한다.
        out.append(Suggestion(label="(", insert="(", kind=SuggestKind.KEYWORD))
    return out


def _booleans(ctx: Context) -> list[Suggestion]:
    if "TRUE" not in ctx.accepts or ctx.spec is None or ctx.spec.type is not FieldType.BOOL:
        return []
    return [
        Suggestion(label=text, insert=f"{text} ", kind=SuggestKind.VALUE)
        for text in ("true", "false")
    ]


def static_candidates(ctx: Context) -> list[Suggestion]:
    """DB 없이 낼 수 있는 후보. 값은 서비스가 따로 붙인다."""
    return [
        *_booleans(ctx),
        *_functions(ctx),
        *_fields(ctx),
        *_operators(ctx),
        *_keywords(ctx),
    ]


def rank(ctx: Context, candidates: list[Suggestion], limit: int) -> list[Suggestion]:
    """접두사로 거르고 종류·자릿수로 줄 세운다.

    앞에서부터 맞는 것이 먼저다. 가운데만 맞는 것도 남긴다 — `user` 로
    `currentUser()` 를 찾을 수 있어야 한다.
    """
    needle = ctx.prefix.lower()
    scored: list[tuple[int, int, int, str, Suggestion]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item.kind.value, item.insert)
        if key in seen:
            continue
        seen.add(key)
        label = item.label.lower()
        if not needle or label.startswith(needle):
            hit = 0
        elif needle in label or needle in item.detail.lower():
            hit = 1
        else:
            continue
        scored.append((hit, _ORDER[item.kind], item.weight, item.label.lower(), item))
    scored.sort(key=lambda row: row[:4])
    return [row[4] for row in scored[:limit]]


__all__ = [
    "Context",
    "SuggestKind",
    "Suggestion",
    "analyze",
    "quote_value",
    "rank",
    "static_candidates",
]
