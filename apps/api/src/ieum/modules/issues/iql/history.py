"""이력 연산자가 무엇을 어디서 찾는지 (M5).

`issue_history.changes` 는 `[{"field": ..., "from": ..., "to": ...}]` 이고,
**거기 적힌 이름과 값의 모양은 IQL 의 것과 다르다.** `status` 는 상태 *이름*
을, `assignee` 는 `assignee_id` 라는 이름 아래 UUID *문자열*을 담는다.

그 대응을 여기 한 곳에 모은다. 틀리면 조건이 조용히 아무것도 안 맞고, 그건
"이력이 없다" 와 구분되지 않는다 — 이 모듈이 시험으로 못 박혀 있는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class WindowKind(StrEnum):
    """시간 창의 모양."""

    #: 언제든. 창을 안 적으면 이것이다.
    ANY = "any"
    DURING = "during"
    AFTER = "after"
    BEFORE = "before"


@dataclass(frozen=True, slots=True)
class HistorySpec:
    """IQL 필드 하나가 이력에서 어떻게 보이는가."""

    #: `changes[].field` 에 적히는 이름. IQL 이름과 다를 수 있다.
    stored_as: str
    #: 사람이 읽는 이름. 오류 메시지에 쓴다.
    label: str


#: 이력 연산자를 쓸 수 있는 필드.
#:
#: **일부러 좁다.** 이력에 UUID 만 남는 필드(`type`·`fixVersion`)를 열어 두면
#: `type WAS "버그"` 가 이름으로는 절대 안 맞는데 오류도 안 난다 — 조용히
#: 빈 결과가 나온다. 이름을 되짚어 줄 수 있는 것만 여기 넣는다.
HISTORY_FIELDS: dict[str, HistorySpec] = {
    # 워크플로우 전이가 상태 **이름**을 적는다 (`service.transition`).
    "status": HistorySpec(stored_as="status", label="상태"),
    # 필드 수정이 컬럼 이름 그대로 적고, 값은 UUID 문자열이다.
    "assignee": HistorySpec(stored_as="assignee_id", label="담당자"),
    "reporter": HistorySpec(stored_as="reporter_id", label="보고자"),
    "priority": HistorySpec(stored_as="priority", label="우선순위"),
}


def stored_value(value: Any) -> str | None:
    """IQL 값을 `changes[].from`/`to` 에 적힌 모양으로.

    JSONB 에서 `->>` 로 꺼내면 무엇이든 **텍스트**다. 그래서 비교도 텍스트로
    한다 — 숫자를 숫자로 비교하려면 캐스팅이 하나 더 필요하고, 그 캐스팅은
    `null` 이 섞인 열에서 터진다.

    `None` 은 "값이 없었다" 를 뜻하고, JSONB 의 `null` 과 맞춰야 한다 —
    호출하는 쪽이 그 경우를 따로 다룬다.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # `_jsonable` 이 파이썬 bool 을 JSON true/false 로 넣는다.
        return "true" if value else "false"
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def supports(field_name: str) -> bool:
    return field_name in HISTORY_FIELDS


def spec_for(field_name: str) -> HistorySpec | None:
    return HISTORY_FIELDS.get(field_name)


__all__ = [
    "HISTORY_FIELDS",
    "HistorySpec",
    "WindowKind",
    "spec_for",
    "stored_value",
    "supports",
]
