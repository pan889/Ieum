"""SLA 정책 해석과 클럭 (feature-map C4·C5).

`calendar.py` 가 시간을 세고, 여기가 **언제 세기 시작하고 언제 멈추는지**를
정한다. 두 층을 나눈 이유는 앞쪽이 순수 함수라 손으로 계산해 시험할 수 있기
때문이다.

클럭을 움직이는 것은 **워커다**(아웃박스). API 요청 경로에서 재면 아무도 열어
보지 않은 티켓은 영원히 위반이 아니게 된다 — 그건 SLA 를 안 재는 것과 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from ieum.modules.desk.calendar import BusinessCalendar, CalendarError

#: 하루의 업무 구간 문자열을 `time` 으로 읽는 형식.
_TIME = "%H:%M"


def parse_calendar(*, timezone: str, working_hours: Any, holidays: Any) -> BusinessCalendar:
    """저장된 JSON 을 계산용 달력으로.

    **여기서 거절한다.** 모양이 틀린 달력이 계산 단계까지 흘러가면 SLA 가
    조용히 이상한 숫자를 내고, 그건 며칠 뒤에 "SLA 가 안 맞는다" 로만 보인다.
    """
    if not isinstance(working_hours, dict):
        raise CalendarError("업무 시간표가 사전이 아니다")
    week: dict[int, tuple[tuple[time, time], ...]] = {}
    for raw_day, raw_spans in working_hours.items():
        try:
            day = int(raw_day)
        except (TypeError, ValueError) as exc:
            raise CalendarError(f"요일이 숫자가 아니다: {raw_day!r}") from exc
        if not isinstance(raw_spans, list):
            raise CalendarError(f"{day} 요일의 구간이 목록이 아니다")
        spans: list[tuple[time, time]] = []
        for span in raw_spans:
            if not (isinstance(span, list) and len(span) == 2):
                raise CalendarError(f"{day} 요일의 구간은 [시작, 끝] 이어야 한다")
            try:
                spans.append(
                    (
                        datetime.strptime(str(span[0]), _TIME).time(),
                        datetime.strptime(str(span[1]), _TIME).time(),
                    )
                )
            except ValueError as exc:
                raise CalendarError(f"시각을 읽을 수 없다: {span!r}") from exc
        week[day] = tuple(spans)

    if not isinstance(holidays, list):
        raise CalendarError("휴일이 목록이 아니다")
    days: set[date] = set()
    for raw in holidays:
        try:
            days.add(date.fromisoformat(str(raw)))
        except ValueError as exc:
            raise CalendarError(f"휴일 날짜를 읽을 수 없다: {raw!r}") from exc

    return BusinessCalendar(timezone=timezone, week=week, holidays=frozenset(days))


@dataclass(frozen=True, slots=True)
class TicketFacts:
    """목표를 고르는 데 쓰는 티켓의 성질.

    조건에 쓸 수 있는 것만 담는다 — 아무 필드나 조건으로 쓸 수 있게 하면
    정책이 이슈 스키마에 묶이고, 필드를 지우는 순간 정책이 말없이 안 맞는다.
    """

    priority: int
    request_type_id: UUID | None
    organization_id: UUID | None


class SlaError(ValueError):
    """정책 정의가 성립하지 않는다."""


def validate_goals(goals: Any) -> list[dict[str, Any]]:
    """목표 목록을 검증해 그대로 돌려준다.

    **저장할 때 부른다.** 목표가 하나도 안 맞는 정책은 클럭을 못 걸고, 그러면
    그 정책에 걸린 티켓 전부가 조용히 SLA 없이 굴러간다 — 큐 조건과 같은
    판단이다(저장되고 실행이 실패하는 것을 만들 수 없게).
    """
    if not isinstance(goals, list) or not goals:
        raise SlaError("목표가 하나도 없다")
    seen_default = False
    out: list[dict[str, Any]] = []
    for goal in goals:
        if not isinstance(goal, dict):
            raise SlaError("목표는 사전이어야 한다")
        seconds = goal.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise SlaError("목표 시간(seconds)은 양의 정수여야 한다")
        unknown = set(goal) - {"seconds", "priority_min", "request_type_id"}
        if unknown:
            raise SlaError(f"모르는 조건: {sorted(unknown)}")
        if "priority_min" in goal:
            value = goal["priority_min"]
            if not isinstance(value, int) or isinstance(value, bool):
                raise SlaError("priority_min 은 정수여야 한다")
        if "request_type_id" in goal:
            try:
                UUID(str(goal["request_type_id"]))
            except ValueError as exc:
                raise SlaError("request_type_id 가 UUID 가 아니다") from exc
        if not ({"priority_min", "request_type_id"} & set(goal)):
            seen_default = True
        out.append(dict(goal))
    if not seen_default:
        # **조건 없는 항목이 하나 있어야 한다.** 없으면 조건에 안 걸리는
        # 티켓은 클럭 없이 굴러가고, 화면에는 SLA 칸이 비어 있을 뿐이라
        # 아무도 그것이 빠졌다는 것을 모른다.
        raise SlaError("조건 없는 기본 목표가 없다")
    return out


def goal_seconds(goals: list[dict[str, Any]], facts: TicketFacts) -> int | None:
    """이 티켓에 맞는 목표 시간. **위에서부터 처음 맞는 것**이 이긴다.

    순서가 규칙이므로 배열이다. 사전으로 두면 "가장 구체적인 것" 같은 암묵
    규칙이 필요해지고, 관리자는 자기가 적은 순서와 다른 결과를 본다.
    """
    for goal in goals:
        if "priority_min" in goal and facts.priority < int(goal["priority_min"]):
            continue
        if "request_type_id" in goal:
            if facts.request_type_id is None:
                continue
            if str(facts.request_type_id) != str(goal["request_type_id"]):
                continue
        seconds = goal.get("seconds")
        if isinstance(seconds, int):
            return seconds
    return None


@dataclass(frozen=True, slots=True)
class Remaining:
    """화면에 그릴 값. 서버가 계산해 내려 준다.

    브라우저가 `target_at` 만 받아 카운트다운하게 두면 **업무 시간이 빠진다** —
    금요일 저녁에 남은 4시간이 토요일 아침에 0 이 된다. 남은 것은 업무 초다.
    """

    #: 남은 업무 초. 위반이면 음수다.
    seconds: int
    breached: bool
    #: 시계가 멈춰 있는가 (고객 답변 대기 등).
    paused: bool
    #: 이미 지킨 약속이면 True — 남은 시간을 그리지 않는다.
    completed: bool


def remaining(
    calendar: BusinessCalendar,
    *,
    target_at: datetime,
    now: datetime,
    paused_at: datetime | None,
    completed_at: datetime | None,
) -> Remaining:
    """남은 업무 시간.

    멈춰 있으면 **멈춘 시각을 기준으로** 잰다. 지금으로 재면 멈춰 있는 동안
    잔여 시간이 계속 줄어들어, 고객 답변을 기다리는 티켓이 저절로 위반된다.
    """
    if completed_at is not None:
        return Remaining(seconds=0, breached=False, paused=False, completed=True)
    reference = paused_at or now
    if reference >= target_at:
        overdue = -working_seconds(calendar, target_at, reference)
        return Remaining(
            seconds=overdue, breached=True, paused=paused_at is not None, completed=False
        )
    left = working_seconds(calendar, reference, target_at)
    return Remaining(seconds=left, breached=False, paused=paused_at is not None, completed=False)


def working_seconds(calendar: BusinessCalendar, start: datetime, end: datetime) -> int:
    """`calendar.working_seconds_between` 의 얇은 별칭. 부르는 쪽이 두 모듈을
    함께 import 하지 않게 한다."""
    from ieum.modules.desk.calendar import working_seconds_between

    return working_seconds_between(calendar, start, end)


def utc(moment: datetime) -> datetime:
    """DB 에서 온 시각을 UTC aware 로. **naive 를 그냥 넘기지 않는다.**

    Postgres 의 `timestamptz` 는 aware 로 오지만, SQLite 나 손으로 만든
    객체가 섞이면 naive 가 들어온다. 달력이 그것을 거절하므로 여기서 한 번
    올린다 — 거절 자체는 달력에 남겨 둔다.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
