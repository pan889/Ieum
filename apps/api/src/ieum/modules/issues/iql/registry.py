"""IQL 필드·함수 레지스트리.

**화이트리스트에만 존재한다** (query-language.md 보안 규칙). 등록되지 않은
필드·함수·정렬 키는 파싱은 되더라도 검증에서 거부된다. 임의 컬럼명이
SQL 로 흘러들어갈 경로를 아예 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from ieum.modules.issues.iql.ast import Operator


class FieldType(StrEnum):
    TEXT = "text"
    #: 정확 일치만 하는 식별자성 값 (상태 이름, 프로젝트 키 등)
    KEYWORD = "keyword"
    USER = "user"
    NUMBER = "number"
    DATE = "date"
    #: 값이 여러 개인 필드 (labels)
    MULTI = "multi"
    BOOL = "bool"


EQUALITY = frozenset({Operator.EQ, Operator.NE, Operator.IN, Operator.NOT_IN})
ORDERING = frozenset({Operator.GT, Operator.GTE, Operator.LT, Operator.LTE})
MATCHING = frozenset({Operator.CONTAINS, Operator.NOT_CONTAINS})

OPERATORS_BY_TYPE: dict[FieldType, frozenset[Operator]] = {
    FieldType.TEXT: EQUALITY | MATCHING,
    FieldType.KEYWORD: EQUALITY,
    FieldType.USER: EQUALITY,
    FieldType.NUMBER: EQUALITY | ORDERING,
    FieldType.DATE: EQUALITY | ORDERING,
    FieldType.MULTI: EQUALITY | MATCHING,
    FieldType.BOOL: frozenset({Operator.EQ, Operator.NE}),
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: FieldType
    description: str
    #: 정렬에 쓸 수 있는가. 리스트성 필드는 정렬 기준이 모호하다.
    sortable: bool = True
    #: IS EMPTY 를 쓸 수 있는가. NOT NULL 컬럼은 의미가 없다.
    nullable: bool = True
    aliases: tuple[str, ...] = ()

    def allows(self, operator: Operator) -> bool:
        return operator in OPERATORS_BY_TYPE[self.type]

    def allowed_operators(self) -> list[str]:
        return sorted(op.value for op in OPERATORS_BY_TYPE[self.type])


#: M1 범위의 필드. 데스크·이력 필드는 각각 M4·M5 에서 추가한다.
FIELDS: dict[str, FieldSpec] = {}


def _register(spec: FieldSpec) -> None:
    FIELDS[spec.name.lower()] = spec
    for alias in spec.aliases:
        FIELDS[alias.lower()] = spec


for _spec in (
    # 정렬은 M1 범위 밖이다. 키로 정렬하려면 org 테이블 조인이 필요한데,
    # 그러면 모듈 경계를 넘는다. 필터링은 키를 id 로 해석해 처리한다.
    FieldSpec("project", FieldType.KEYWORD, "프로젝트 키", nullable=False, sortable=False),
    FieldSpec("key", FieldType.KEYWORD, "이슈 키 순번", nullable=False),
    FieldSpec("type", FieldType.KEYWORD, "이슈 유형 이름", nullable=False, aliases=("issuetype",)),
    FieldSpec("status", FieldType.KEYWORD, "상태 이름", nullable=False),
    FieldSpec(
        "statuscategory",
        FieldType.KEYWORD,
        "상태 분류 (todo/in_progress/done)",
        nullable=False,
    ),
    FieldSpec("summary", FieldType.TEXT, "제목", nullable=False),
    FieldSpec("description", FieldType.TEXT, "본문"),
    FieldSpec("priority", FieldType.NUMBER, "우선순위 1~5", nullable=False),
    FieldSpec("assignee", FieldType.USER, "담당자"),
    FieldSpec("reporter", FieldType.USER, "보고자"),
    FieldSpec("labels", FieldType.MULTI, "라벨", sortable=False),
    FieldSpec("parent", FieldType.KEYWORD, "상위 이슈 키 순번"),
    FieldSpec("created", FieldType.DATE, "생성 시각", nullable=False),
    FieldSpec("updated", FieldType.DATE, "수정 시각", nullable=False),
    FieldSpec("due", FieldType.DATE, "마감일", aliases=("duedate",)),
    FieldSpec("startdate", FieldType.DATE, "시작일"),
    FieldSpec("resolved", FieldType.DATE, "완료 시각", aliases=("resolutiondate",)),
    FieldSpec("estimate", FieldType.NUMBER, "추정 공수(분)"),
    FieldSpec("progress", FieldType.NUMBER, "진행률 0~100", nullable=False),
    FieldSpec("archived", FieldType.BOOL, "아카이브 여부", sortable=False, nullable=False),
):
    _register(_spec)


def known_field_names() -> list[str]:
    return sorted(FIELDS)


# ── 함수 ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FunctionContext:
    """함수 평가에 필요한 것. 실행자 기준으로 계산한다."""

    actor_id: UUID
    #: 사용자 타임존. 날짜 경계 함수가 이 기준으로 하루를 자른다.
    timezone: str = "UTC"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    name: str
    returns: FieldType
    description: str
    #: 인자 개수 범위 (최소, 최대). 날짜 함수는 오프셋 0~1개.
    arity: tuple[int, int]
    evaluate: Callable[[FunctionContext, list[Any]], Any]


def _offset(args: list[Any]) -> int:
    return int(args[0]) if args else 0


def _day_start(ctx: FunctionContext, days: int) -> datetime:
    base = ctx.now + timedelta(days=days)
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _day_end(ctx: FunctionContext, days: int) -> datetime:
    return _day_start(ctx, days) + timedelta(days=1) - timedelta(microseconds=1)


def _week_start(ctx: FunctionContext, weeks: int) -> datetime:
    start = _day_start(ctx, weeks * 7)
    # 월요일 시작. ISO 기준이라 한국·유럽 사용자의 기대와 맞는다.
    return start - timedelta(days=start.weekday())


def _month_start(ctx: FunctionContext, months: int) -> datetime:
    base = _day_start(ctx, 0).replace(day=1)
    year, month = divmod(base.month - 1 + months, 12)
    return base.replace(year=base.year + year, month=month + 1)


def _month_end(ctx: FunctionContext, months: int) -> datetime:
    nxt = _month_start(ctx, months + 1)
    return nxt - timedelta(microseconds=1)


FUNCTIONS: dict[str, FunctionSpec] = {}


def _register_fn(spec: FunctionSpec) -> None:
    FUNCTIONS[spec.name.lower()] = spec


for _fn in (
    FunctionSpec(
        "currentUser",
        FieldType.USER,
        "요청한 사용자",
        (0, 0),
        lambda ctx, _a: ctx.actor_id,
    ),
    FunctionSpec("now", FieldType.DATE, "현재 시각", (0, 0), lambda ctx, _a: ctx.now),
    FunctionSpec(
        "startOfDay",
        FieldType.DATE,
        "오늘 0시(±n일)",
        (0, 1),
        lambda ctx, a: _day_start(ctx, _offset(a)),
    ),
    FunctionSpec(
        "endOfDay",
        FieldType.DATE,
        "오늘 24시(±n일)",
        (0, 1),
        lambda ctx, a: _day_end(ctx, _offset(a)),
    ),
    FunctionSpec(
        "startOfWeek",
        FieldType.DATE,
        "이번 주 월요일(±n주)",
        (0, 1),
        lambda ctx, a: _week_start(ctx, _offset(a)),
    ),
    FunctionSpec(
        "endOfWeek",
        FieldType.DATE,
        "이번 주 일요일 끝(±n주)",
        (0, 1),
        lambda ctx, a: _week_start(ctx, _offset(a)) + timedelta(days=7) - timedelta(microseconds=1),
    ),
    FunctionSpec(
        "startOfMonth",
        FieldType.DATE,
        "이번 달 1일(±n달)",
        (0, 1),
        lambda ctx, a: _month_start(ctx, _offset(a)),
    ),
    FunctionSpec(
        "endOfMonth",
        FieldType.DATE,
        "이번 달 말일 끝(±n달)",
        (0, 1),
        lambda ctx, a: _month_end(ctx, _offset(a)),
    ),
):
    _register_fn(_fn)


def known_function_names() -> list[str]:
    return sorted(FUNCTIONS[k].name for k in FUNCTIONS)


def coerce_date(value: Any) -> datetime | date | None:
    """문자열 날짜를 파싱한다. 실패하면 None."""
    if isinstance(value, datetime | date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
