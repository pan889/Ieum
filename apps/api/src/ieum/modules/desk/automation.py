"""자동화 규칙 — 순수 함수만 (feature-map C9).

`sla.py`·`email.py` 와 같은 층 나누기다: 조건을 판정하는 계산은 입력과 출력이
전부이므로 손으로 값을 적어 시험할 수 있다. 행을 쓰는 것은 `rules.py` 다.

## 조건을 IQL 로 쓰지 않는 이유

큐는 IQL 을 쓴다(`queue.iql`). 자동화도 그렇게 하고 싶었지만 둘은 다른
질문을 한다:

- IQL 은 **저장된 이슈들**에 대한 질의다. 실행자의 권한으로 돌고, 결과는
  목록이다.
- 자동화 조건은 **한 티켓의 지금**에 대한 판정이다. 실행자가 없고(워커다),
  결과는 참·거짓이며, **이벤트가 들고 온 사실**도 본다 — "방금 done 으로
  갔다" 는 저장된 상태만으로는 알 수 없다.

IQL 로 판정하려면 워커용 시스템 액터를 만들어 권한 검사를 건너뛰어야 하고,
그건 "질의 하나로 권한을 우회하는 길" 을 여는 일이다. 조건 언어를 둘 두는
비용을 치르고 그 길을 안 만든다.

## 조치는 등록된 이름 + 파라미터로만

워크플로우 엔진·SLA 에스컬레이션과 같은 규약이다 (module-guide).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


class AutomationError(Exception):
    """성립하지 않는 규칙. 저장할 때 거절한다."""


#: 규칙을 깨우는 이벤트. 아웃박스의 이름 그대로다.
TRIGGERS = (
    "desk.ticket.submitted",
    "issue.transitioned",
    "issue.commented",
)

#: 조건에서 볼 수 있는 것. 늘릴 때는 `TicketFacts` 와 `_value_of` 도 같이 는다.
FIELDS = (
    "priority",
    "channel",
    "request_type_id",
    "organization_id",
    "state_category",
    "summary",
    #: 이벤트가 들고 온 사실. 저장된 상태만으로는 알 수 없다.
    "is_internal",
    "to_state_category",
)

#: 비교 방법.
OPERATORS = ("eq", "ne", "gte", "lte", "in", "contains")

#: 항목이 가진 값의 **종류.**
#:
#: 이게 없으면 `is_internal eq "true"` 가 저장된다 — 그리고 영원히 안 맞는다.
#: `_same` 이 불리언 한쪽을 `is` 로 비교하기 때문이다(`True is "true"` 는
#: 거짓). 자동화가 안 도는 것과 조건이 안 맞는 것은 화면에서 구별되지
#: 않으므로, 저장하는 자리에서 종류를 맞춰 본다.
FIELD_KINDS: dict[str, str] = {
    "priority": "int",
    "channel": "channel",
    "request_type_id": "uuid",
    "organization_id": "uuid",
    "state_category": "category",
    "summary": "str",
    "is_internal": "bool",
    "to_state_category": "category",
}

#: 종류마다 쓸 수 있는 비교.
#:
#: 크기 비교를 문자열에 걸면 `_one_matches` 가 언제나 거짓을 낸다. UUID 에
#: `contains` 를 걸면 우연히 겹치는 조각으로 맞는다 — 둘 다 뜻이 없다.
OPS_FOR_KIND: dict[str, tuple[str, ...]] = {
    "int": ("eq", "ne", "gte", "lte", "in"),
    "str": ("eq", "ne", "contains", "in"),
    "uuid": ("eq", "ne", "in"),
    "bool": ("eq", "ne"),
    "channel": ("eq", "ne", "in"),
    "category": ("eq", "ne", "in"),
}

#: 값이 정해져 있는 항목의 어휘.
#:
#: **손으로 두 번 적은 목록이다.** 원본은 `desk.models.TICKET_CHANNELS` 와
#: `issues.contracts.STATE_CATEGORIES` 이고, 이 층은 순수하게 두려고 그걸
#: 안 부른다 — 대신 `test_desk_automation.py` 가 두 쪽이 같은지 못박는다.
#: 원본에 하나 늘면 그 시험이 붉어진다.
CHANNELS = ("portal", "email", "agent")
CATEGORIES = ("todo", "in_progress", "done")

#: 무엇을 할 수 있는가.
ACTIONS = ("set_priority", "assign", "reply_with_canned", "add_note")

#: 한 규칙의 조건 수 상한. 넘으면 규칙이 아니라 질의다 — 그건 큐가 한다.
MAX_CONDITIONS = 10
MAX_ACTIONS = 5


@dataclass(frozen=True, slots=True)
class TicketFacts:
    """조건이 보는 것 전부.

    이벤트가 들고 온 것과 티켓에 저장된 것을 **한 자리에 모은다.** 조건
    판정이 DB 를 다시 읽으면 순수 함수가 아니게 되고, 손으로 값을 적어
    시험할 수 없다.
    """

    priority: int
    channel: str
    summary: str
    request_type_id: UUID | None = None
    organization_id: UUID | None = None
    state_category: str = ""
    #: 이 이벤트가 내부 노트였는가 (`issue.commented`).
    is_internal: bool = False
    #: 어느 상태로 갔는가 (`issue.transitioned`).
    to_state_category: str = ""


@dataclass(frozen=True, slots=True)
class Action:
    """실행할 조치 하나."""

    kind: str
    #: `set_priority` 가 정할 값.
    priority: int | None = None
    #: `assign` 이 지목한 사람.
    user_id: UUID | None = None
    #: `reply_with_canned`·`add_note` 가 쓸 정형 응답.
    canned_response_id: UUID | None = None


def validate_trigger(trigger: Any) -> dict[str, Any]:
    """규칙을 깨우는 이벤트. 저장할 때 부른다."""
    if not isinstance(trigger, dict):
        raise AutomationError("트리거가 사전이 아니다")
    unknown = set(trigger) - {"event"}
    if unknown:
        raise AutomationError(f"모르는 항목: {sorted(unknown)}")
    event = trigger.get("event")
    if event not in TRIGGERS:
        raise AutomationError(f"모르는 트리거: {event!r}")
    return {"event": event}


def validate_conditions(conditions: Any) -> list[dict[str, Any]]:
    """조건들. **전부 맞아야 한다(AND).**

    OR 을 안 넣는 이유: 규칙을 둘로 나누면 표현할 수 있고, 그 편이 목록에서
    읽힌다. 한 규칙 안에 AND 와 OR 이 섞이면 괄호가 필요해지고, 그 순간
    이것은 질의 언어가 된다 — 그건 IQL 이 하는 일이다.

    빈 목록은 정상이다: "이 이벤트면 언제나" 를 뜻한다.
    """
    if conditions is None:
        return []
    if not isinstance(conditions, list):
        raise AutomationError("조건이 목록이 아니다")
    if len(conditions) > MAX_CONDITIONS:
        raise AutomationError(f"조건은 {MAX_CONDITIONS}개까지다")
    out: list[dict[str, Any]] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            raise AutomationError("조건은 사전이어야 한다")
        unknown = set(condition) - {"field", "op", "value"}
        if unknown:
            raise AutomationError(f"모르는 항목: {sorted(unknown)}")
        field = condition.get("field")
        if field not in FIELDS:
            raise AutomationError(f"모르는 조건 항목: {field!r}")
        op = condition.get("op")
        if op not in OPERATORS:
            raise AutomationError(f"모르는 비교: {op!r}")
        if "value" not in condition:
            raise AutomationError("비교할 값이 없다")
        value = condition["value"]
        if op == "in" and not isinstance(value, list):
            raise AutomationError("in 의 값은 목록이어야 한다")
        if op in ("gte", "lte") and not isinstance(value, int):
            raise AutomationError(f"{op} 의 값은 정수여야 한다")
        if op == "contains" and not isinstance(value, str):
            raise AutomationError("contains 의 값은 문자열이어야 한다")
        out.append({"field": field, "op": op, "value": _checked_value(field, op, value)})
    return out


def validate_actions(actions: Any) -> list[dict[str, Any]]:
    """조치들. **하나는 있어야 한다.**

    조치 없는 규칙은 조건만 맞춰 보고 아무 것도 안 한다 — 저장은 되고 아무
    일도 일어나지 않는 것을 만들지 않는다.
    """
    if not isinstance(actions, list) or not actions:
        raise AutomationError("조치가 하나도 없다")
    if len(actions) > MAX_ACTIONS:
        raise AutomationError(f"조치는 {MAX_ACTIONS}개까지다")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            raise AutomationError("조치는 사전이어야 한다")
        unknown = set(action) - {"kind", "priority", "user_id", "canned_response_id"}
        if unknown:
            raise AutomationError(f"모르는 항목: {sorted(unknown)}")
        kind = action.get("kind")
        if kind not in ACTIONS:
            raise AutomationError(f"모르는 조치: {kind!r}")
        # **같은 조치를 둘 두지 않는다.** 우선순위를 3 으로 정하고 5 로도
        # 정하는 규칙은 뜻이 없고, 어느 쪽이 이기는지는 배열 순서라는
        # 보이지 않는 규칙이 된다.
        if kind in seen:
            raise AutomationError(f"같은 조치가 둘 있다: {kind}")
        seen.add(kind)

        if kind == "set_priority":
            value = action.get("priority")
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise AutomationError("set_priority 의 priority 는 1..5 여야 한다")
        if kind == "assign":
            _require_uuid(action, "user_id", "assign")
        if kind in ("reply_with_canned", "add_note"):
            _require_uuid(action, "canned_response_id", kind)
        out.append(dict(action))
    return out


def parse_action(action: dict[str, Any]) -> Action:
    """저장된 사전을 조치 객체로. 검증을 통과한 것만 넘긴다."""
    return Action(
        kind=str(action["kind"]),
        priority=int(action["priority"]) if action.get("priority") is not None else None,
        user_id=UUID(str(action["user_id"])) if action.get("user_id") else None,
        canned_response_id=(
            UUID(str(action["canned_response_id"])) if action.get("canned_response_id") else None
        ),
    )


def matches(conditions: list[dict[str, Any]], facts: TicketFacts) -> bool:
    """조건이 전부 맞는가. 빈 조건은 언제나 맞는다."""
    return all(_one_matches(condition, facts) for condition in conditions)


# ── 내부 ────────────────────────────────────────────────────────


def _checked_value(field: str, op: str, value: Any) -> Any:
    """값이 이 항목에 쓸 수 있는 것인가. 쓸 수 있으면 **다듬어** 돌려준다.

    UUID 는 정규형(소문자)으로 맞춘다. 화면이 고른 것을 되돌려 받아 `select`
    의 값과 비교하는데, 대문자로 저장돼 있으면 아무 것도 안 골라진 것처럼
    보인다.
    """
    kind = FIELD_KINDS[field]
    if op not in OPS_FOR_KIND[kind]:
        raise AutomationError(f"{field} 에는 {op} 를 쓸 수 없다")
    if op != "in":
        return _checked_one(field, kind, value)
    if not value:
        raise AutomationError(f"{field} 의 in 이 비어 있다")
    return [_checked_one(field, kind, item) for item in value]


def _checked_one(field: str, kind: str, value: Any) -> Any:
    if kind == "bool":
        if not isinstance(value, bool):
            raise AutomationError(f"{field} 의 값은 참·거짓이어야 한다")
        return value
    if kind == "int":
        # `True` 는 `int` 다. 우선순위가 참이 되게 두지 않는다.
        if not isinstance(value, int) or isinstance(value, bool):
            raise AutomationError(f"{field} 의 값은 정수여야 한다")
        return value
    if kind == "uuid":
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AutomationError(f"{field} 의 값이 UUID 가 아니다") from exc
    if not isinstance(value, str):
        raise AutomationError(f"{field} 의 값은 문자열이어야 한다")
    if kind == "channel" and value not in CHANNELS:
        raise AutomationError(f"모르는 경로: {value!r}")
    if kind == "category" and value not in CATEGORIES:
        raise AutomationError(f"모르는 상태 분류: {value!r}")
    return value


def _require_uuid(action: dict[str, Any], key: str, kind: str) -> None:
    raw = action.get(key)
    if raw is None:
        raise AutomationError(f"{kind} 에는 {key} 가 필요하다")
    try:
        UUID(str(raw))
    except ValueError as exc:
        raise AutomationError(f"{key} 가 UUID 가 아니다") from exc


def _one_matches(condition: dict[str, Any], facts: TicketFacts) -> bool:
    actual = _value_of(condition["field"], facts)
    expected = condition["value"]
    op = condition["op"]

    if op == "eq":
        return _same(actual, expected)
    if op == "ne":
        return not _same(actual, expected)
    if op == "in":
        return any(_same(actual, item) for item in expected)
    if op == "contains":
        return str(expected).lower() in str(actual or "").lower()
    # 크기 비교는 숫자만. 문자열에 적용하면 사전 순으로 비교되고, 그건
    # 관리자가 뜻한 것이 아니다.
    if not isinstance(actual, int) or isinstance(actual, bool):
        return False
    if not isinstance(expected, int) or isinstance(expected, bool):
        return False
    return actual >= expected if op == "gte" else actual <= expected


def _value_of(field: str, facts: TicketFacts) -> object:
    return {
        "priority": facts.priority,
        "channel": facts.channel,
        "request_type_id": facts.request_type_id,
        "organization_id": facts.organization_id,
        "state_category": facts.state_category,
        "summary": facts.summary,
        "is_internal": facts.is_internal,
        "to_state_category": facts.to_state_category,
    }[field]


def _same(actual: object, expected: object) -> bool:
    """값 비교. **UUID 는 문자열로 온다** — JSON 에 UUID 타입이 없다.

    문자열끼리 비교하면 대소문자가 다른 두 UUID 가 다른 것이 되므로, 한쪽이
    UUID 면 양쪽을 UUID 로 맞춘다.
    """
    if isinstance(actual, UUID):
        try:
            return actual == UUID(str(expected))
        except (ValueError, TypeError):
            return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        # `True == 1` 이 참이라 우선순위 1 이 "내부 노트임" 과 같아진다.
        return actual is expected
    return actual == expected
