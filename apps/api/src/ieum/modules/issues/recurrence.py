"""반복 이슈의 스케줄 계산 (A27, M5).

## 크론을 사람에게 받지 않는다

`0 9 * * 1` 을 읽고 쓸 수 있는 사람은 우리 사용자의 일부이고, 틀리게 적은
크론은 **조용히 안 돈다.** 그래서 어휘를 좁힌다: 매일 / 매주(요일) / 매월(일)
과 시각. 이 셋으로 표현되지 않는 주기(격주, 마지막 금요일)는 지금 없다 —
필요해지면 어휘를 늘리는 편이, 크론을 열어 두고 "왜 안 돌아요" 를 받는 편보다
싸다.

## 시간대를 스케줄이 갖는다

"매주 월요일 9시" 는 **어디의 9시인가.** 보는 사람의 시간대로 재면 같은
스케줄이 사람마다 다른 시각에 뜨고, 서버의 UTC 로 재면 서울의 9시가 오후 6시가
된다. 그래서 만들 때 시간대를 함께 적고, 계산은 그 시간대에서 한다.

## 일광절약시간에서 무슨 일이 벌어지나 — 정해 두고 시험한다

없는 시각과 두 번 있는 시각이 있다. 파이썬의 `zoneinfo` 기본값을 그대로
쓰되, **무엇이 되는지 적어 둔다** (아래 두 함수의 시험이 그것을 붙잡는다):

- **없는 시각**(봄, 2시→3시로 뛰는 날의 2:30): 전환 전 오프셋으로 해석되어
  그날은 벽시계 3:30 에 돈다. 건너뛰지 않는다.
- **두 번 있는 시각**(가을, 1:30 이 두 번 오는 날): 앞의 것에 한 번 돈다
  (`fold=0`). 두 번 돌지 않는다.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ieum.core.exceptions import ValidationError

#: 이 셋만 받는다. 모듈 주석의 "크론을 사람에게 받지 않는다" 참조.
Cadence = Literal["daily", "weekly", "monthly"]

CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly")


@dataclass(frozen=True, slots=True)
class Schedule:
    """언제 도는가. **시간대를 포함해서** 하나의 값이다."""

    cadence: Cadence
    #: 지역 시각(시·분). UTC 가 아니다.
    hour: int
    minute: int
    timezone: str
    #: 매주면 필수. 0=월 … 6=일 (`datetime.weekday()` 와 같다).
    weekday: int | None = None
    #: 매월이면 필수. 1~31. 짧은 달에서는 **그 달의 마지막 날로 당긴다**
    #: (`next_after` 참조).
    day: int | None = None


def validate(schedule: Schedule) -> None:
    """만들 때 본다. 돌 때 처음 알면 아무 일도 안 일어난 것만 보인다."""
    if schedule.cadence not in CADENCES:
        raise ValidationError(
            "그런 주기는 없다.",
            code="issues.recurrence_unknown_cadence",
            details={"value": schedule.cadence, "allowed": ", ".join(CADENCES)},
        )
    if not 0 <= schedule.hour <= 23 or not 0 <= schedule.minute <= 59:
        raise ValidationError(
            "시각이 하루 안에 있어야 한다.",
            code="issues.recurrence_bad_time",
            details={"hour": schedule.hour, "minute": schedule.minute},
        )
    try:
        ZoneInfo(schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(
            "그런 시간대는 없다.",
            code="issues.recurrence_unknown_timezone",
            details={"value": schedule.timezone},
        ) from exc

    if schedule.cadence == "weekly" and (
        schedule.weekday is None or not 0 <= schedule.weekday <= 6
    ):
        raise ValidationError(
            "매주 반복에는 요일이 필요하다.",
            code="issues.recurrence_needs_weekday",
            details={"value": str(schedule.weekday)},
        )
    if schedule.cadence == "monthly" and (schedule.day is None or not 1 <= schedule.day <= 31):
        raise ValidationError(
            "매월 반복에는 날짜가 필요하다.",
            code="issues.recurrence_needs_day",
            details={"value": str(schedule.day)},
        )


def next_after(schedule: Schedule, after: datetime) -> datetime:
    """`after` **다음**의 실행 시각. UTC 로 돌려준다.

    같은 시각을 돌려주지 않는다(엄격히 뒤). 그래서 "돌고 나서 다음을 구한다"
    가 제자리를 맴돌지 않는다.

    **밀린 것을 몰아 만들지 않는 것은 부르는 쪽의 규칙이다.** 워커는 이 함수에
    `지금` 을 넘긴다 — 놓친 실행 시각을 넘기면 그때부터 하나씩 따라잡으며
    같은 이슈를 여러 건 만든다. 앱이 일주일 내려갔다 올라온 날 매일 스케줄이
    7건을 만드는 것은 복구가 아니라 알림 폭탄이다.
    """
    zone = ZoneInfo(schedule.timezone)
    local = after.astimezone(zone)

    if schedule.cadence == "daily":
        candidate = _at(local, local.year, local.month, local.day, schedule, zone)
        if candidate <= after:
            tomorrow = local.date() + timedelta(days=1)
            candidate = _at(local, tomorrow.year, tomorrow.month, tomorrow.day, schedule, zone)
        return candidate.astimezone(UTC)

    if schedule.cadence == "weekly":
        assert schedule.weekday is not None  # validate() 가 보장한다
        # 오늘부터 이레를 본다. 오늘이 그 요일인데 시각이 지났으면 다음 주다.
        for ahead in range(8):
            date = local.date() + timedelta(days=ahead)
            if date.weekday() != schedule.weekday:
                continue
            candidate = _at(local, date.year, date.month, date.day, schedule, zone)
            if candidate > after:
                return candidate.astimezone(UTC)
        raise AssertionError("이레 안에 그 요일이 없을 수 없다")  # pragma: no cover

    assert schedule.day is not None  # validate() 가 보장한다
    year, month = local.year, local.month
    for _ in range(2):
        # **짧은 달에서는 당긴다.** 건너뛰면 "매월 31일" 이 2월에 안 돌고,
        # 그건 사람이 뜻한 것이 아니다 — 그 달의 마지막 날을 뜻했다.
        day = min(schedule.day, calendar.monthrange(year, month)[1])
        candidate = _at(local, year, month, day, schedule, zone)
        if candidate > after:
            return candidate.astimezone(UTC)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise AssertionError("두 달 안에 다음 실행이 없을 수 없다")  # pragma: no cover


def _at(
    _local: datetime, year: int, month: int, day: int, schedule: Schedule, zone: ZoneInfo
) -> datetime:
    """그 날짜의 지역 시각. 없는 시각·두 번 있는 시각은 모듈 주석 참조."""
    return datetime(year, month, day, schedule.hour, schedule.minute, tzinfo=zone)


__all__ = ["CADENCES", "Cadence", "Schedule", "next_after", "validate"]
