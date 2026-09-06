"""IQL 추상 구문 트리.

파서와 컴파일러 사이의 유일한 계약이다. 저장하지 않는다 — `saved_filter` 는
원문 문자열만 담는다(문법이 진화하므로, query-language.md 6절).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Operator(StrEnum):
    EQ = "="
    NE = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    CONTAINS = "~"
    NOT_CONTAINS = "!~"
    IN = "in"
    NOT_IN = "not in"


class Emptiness(StrEnum):
    EMPTY = "empty"
    NOT_EMPTY = "not empty"


@dataclass(frozen=True, slots=True)
class Span:
    """원문에서의 위치. 에러 응답에 반드시 포함한다 (query-language.md 4절)."""

    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class FieldRef:
    name: str
    #: cf["key"] 형태면 커스텀 필드 키
    custom_key: str | None = None
    span: Span | None = None

    @property
    def is_custom(self) -> bool:
        return self.custom_key is not None

    def label(self) -> str:
        return f'cf["{self.custom_key}"]' if self.custom_key else self.name


@dataclass(frozen=True, slots=True)
class Literal:
    value: Any
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class ListValue:
    items: list[Literal | FunctionCall]
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class FunctionCall:
    name: str
    args: list[Literal] = field(default_factory=list)
    span: Span | None = None


Value = Literal | ListValue | FunctionCall


@dataclass(frozen=True, slots=True)
class Comparison:
    field: FieldRef
    operator: Operator
    value: Value
    span: Span | None = None
    #: 연산자 자체의 위치. "이 필드엔 그 연산자를 못 쓴다" 는 오류가 조건
    #: 전체를 가리키면 어느 글자를 고쳐야 하는지 여전히 알려주지 않는다.
    operator_span: Span | None = None


@dataclass(frozen=True, slots=True)
class EmptinessCheck:
    field: FieldRef
    kind: Emptiness
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class Not:
    operand: Node


@dataclass(frozen=True, slots=True)
class And:
    operands: list[Node]


@dataclass(frozen=True, slots=True)
class Or:
    operands: list[Node]


Node = Comparison | EmptinessCheck | Not | And | Or


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class SortKey:
    field: FieldRef
    direction: SortDirection = SortDirection.ASC


@dataclass(frozen=True, slots=True)
class Query:
    """파싱 결과. where 가 None 이면 조건 없이 전체(ACL 필터는 별도로 붙는다)."""

    where: Node | None
    order_by: list[SortKey] = field(default_factory=list)
    source: str = ""
