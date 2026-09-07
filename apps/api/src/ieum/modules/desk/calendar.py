"""업무 시간 계산 (feature-map C4).

**SLA 는 벽시계로 재지 않는다.** 금요일 오후 5시에 들어온 요청에 "4시간 안에
응답" 을 벽시계로 재면 월요일 아침 9시에 이미 위반이다 — 아무도 일하지 않은
시간을 세었기 때문이다. 그래서 달력이 필요하다.

이 모듈은 **순수 함수**다. 데이터베이스도 세션도 보지 않는다. SLA 의 모든
숫자가 여기에 걸려 있어서, 시험이 값을 손으로 계산해 확인할 수 있어야 한다.

함수 둘이 서로의 역이다.

- `working_seconds_between(a, b)` — 두 시각 사이의 **업무 초**
- `add_working_seconds(a, n)` — a 에서 업무 초 n 이 지난 **시각**

목표 시각을 잡을 때 두 번째를, 남은 시간을 보여 줄 때 첫 번째를 쓴다. 하나로
합칠 수 없다: 목표 시각은 저장해 두어야 하고(달력을 고쳐도 과거 티켓의 판정이
바뀌면 안 된다) 남은 시간은 매번 계산해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: 하루치 업무 구간. `(시작, 끝)` 을 현지 시각으로 담는다. 여러 구간을 두는
#: 것은 점심시간을 빼기 위한 것이다 — 09:00~12:00, 13:00~18:00 처럼.
DaySpans = tuple[tuple[time, time], ...]

#: 요일 → 구간들. 월요일이 0 이다(`date.weekday()`).
WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)


class CalendarError(ValueError):
    """달력 정의가 성립하지 않는다."""


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    """업무 시간표.

    타임존을 **달력이 갖는다.** 사용자 타임존으로 재면 같은 티켓의 SLA 가 보는
    사람마다 달라진다 — SLA 는 조직이 고객에게 한 약속이고, 그 약속의 시계는
    하나여야 한다.
    """

    timezone: str
    #: 요일별 업무 구간. 비어 있는 요일은 휴일이다.
    week: dict[int, DaySpans]
    #: 통째로 쉬는 날. 현지 날짜다 — UTC 로 두면 시차만큼 어긋난 날이 쉰다.
    holidays: frozenset[date] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # `ZoneInfo` 는 없는 이름에 `ZoneInfoNotFoundError` 를, 이상한 모양에
        # `ValueError` 를 던진다. 둘 다 "이 달력은 성립하지 않는다" 로 같다.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarError(f"알 수 없는 타임존: {self.timezone}") from exc
        for day, spans in self.week.items():
            if day not in WEEKDAYS:
                raise CalendarError(f"요일은 0~6 이다: {day}")
            last_end: time | None = None
            for start, end in spans:
                if start >= end:
                    raise CalendarError("업무 구간의 시작이 끝보다 늦다")
                if last_end is not None and start < last_end:
                    raise CalendarError("업무 구간이 겹친다")
                last_end = end
        if not any(self.week.get(day) for day in WEEKDAYS):
            # 업무 시간이 하루도 없으면 클럭이 영원히 멈춘다. 목표 시각도
            # 못 잡고, 그 상태로 저장되면 그 정책에 걸린 티켓 전부가 조용히
            # SLA 없이 굴러간다.
            raise CalendarError("업무 시간이 하루도 없다")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def spans_on(self, day: date) -> DaySpans:
        """이 날짜의 업무 구간. 휴일이면 빈 튜플이다."""
        if day in self.holidays:
            return ()
        return self.week.get(day.weekday(), ())


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    """UTC 시각을 달력의 현지 시각으로. **naive 를 받지 않는다.**

    naive datetime 을 UTC 로 가정하면, 어딘가에서 현지 시각을 그대로 넘긴
    순간 조용히 시차만큼 틀린 답이 나온다. 그 종류는 며칠 뒤에 "SLA 가 이상
    하다" 로만 보인다.
    """
    if moment.tzinfo is None:
        raise CalendarError("timezone 없는 시각은 받지 않는다")
    return moment.astimezone(tz)


def _windows(calendar: BusinessCalendar, day: date) -> list[tuple[datetime, datetime]]:
    """이 현지 날짜의 업무 구간을 현지 aware datetime 쌍으로."""
    tz = calendar.tz
    out: list[tuple[datetime, datetime]] = []
    for start, end in calendar.spans_on(day):
        out.append(
            (
                datetime.combine(day, start, tzinfo=tz),
                datetime.combine(day, end, tzinfo=tz),
            )
        )
    return out


#: 한 번에 훑는 날의 상한. 업무 시간이 아주 드문 달력(주 1회 등)에서도
#: 답이 나오게 넉넉히 두되, 무한 반복은 막는다.
MAX_DAYS = 4000


def working_seconds_between(calendar: BusinessCalendar, start: datetime, end: datetime) -> int:
    """`start` 부터 `end` 까지의 업무 초.

    `end` 가 `start` 보다 이르면 0 이다 — 음수를 돌려주면 부르는 쪽이 그것을
    "남은 시간" 으로 더해 버린다.
    """
    # **naive 검사가 비교보다 먼저다.** 처음에는 `end <= start` 를 먼저
    # 두었고, naive 와 aware 를 비교하는 순간 `TypeError` 가 났다 —
    # `CalendarError` 로 거절하려던 자리를 지나쳐 버린 것이다. 그러면 부르는
    # 쪽은 "달력 정의가 틀렸다" 가 아니라 알 수 없는 타입 오류를 본다.
    tz = calendar.tz
    a = _local(start, tz)
    b = _local(end, tz)
    if b <= a:
        return 0

    total = 0
    day = a.date()
    for _ in range(MAX_DAYS):
        if day > b.date():
            break
        for window_start, window_end in _windows(calendar, day):
            lo = max(window_start, a)
            hi = min(window_end, b)
            if hi > lo:
                total += int((hi - lo).total_seconds())
        day += timedelta(days=1)
    else:
        raise CalendarError("업무 시간 계산이 상한을 넘었다")
    return total


def add_working_seconds(calendar: BusinessCalendar, start: datetime, seconds: int) -> datetime:
    """`start` 에서 업무 초 `seconds` 가 지난 시각 (UTC).

    `seconds` 가 0 이면 **다음 업무 시각**을 돌려준다. 그냥 `start` 를 주면,
    업무 시간 밖에 들어온 요청의 목표 시각이 업무 시간 밖에 놓여 접수 즉시
    위반이 된다.
    """
    if seconds < 0:
        raise CalendarError("음수 업무 초는 받지 않는다")
    tz = calendar.tz
    cursor = _local(start, tz)
    remaining = seconds

    day = cursor.date()
    for _ in range(MAX_DAYS):
        for window_start, window_end in _windows(calendar, day):
            if window_end <= cursor:
                continue
            lo = max(window_start, cursor)
            available = int((window_end - lo).total_seconds())
            if remaining == 0:
                # 다음 업무 시각이 곧 답이다.
                return lo.astimezone(UTC)
            if available >= remaining:
                return (lo + timedelta(seconds=remaining)).astimezone(UTC)
            remaining -= available
        day += timedelta(days=1)
    raise CalendarError("목표 시각 계산이 상한을 넘었다")
