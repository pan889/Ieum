"""반복 이슈의 스케줄 계산 (feature-map A27).

값을 손으로 적는다. 계산이 제품의 약속일 때는 코드로 기대값을 만들면 같은
버그를 두 번 쓴다 (conventions "계산이 제품의 약속일 때는 값을 손으로 적는다").

붙잡는 것:

- **시간대는 스케줄의 것이다.** 서버의 UTC 로 재면 서울의 9시가 오후 6시가
  되고, 보는 사람의 시간대로 재면 사람마다 다른 시각에 뜬다.
- **짧은 달에서는 당긴다.** "매월 31일" 이 2월에 안 돌면 그건 사람이 뜻한
  것이 아니다.
- **일광절약시간에 무엇이 되는지 정해 뒀다.** 없는 시각은 밀려서 돌고, 두 번
  있는 시각은 한 번만 돈다.
- **같은 시각을 돌려주지 않는다.** 그러면 "돌고 나서 다음을 구한다" 가
  제자리를 맴돈다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ieum.core.exceptions import ValidationError
from ieum.modules.issues.recurrence import Schedule, next_after, validate

SEOUL = "Asia/Seoul"
NEW_YORK = "America/New_York"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class TestDaily:
    def test_it_fires_later_the_same_day(self) -> None:
        # 서울 9시는 UTC 0시다. UTC 로 재면 오후 6시가 되어 하루가 어긋난다.
        schedule = Schedule(cadence="daily", hour=9, minute=0, timezone=SEOUL)
        assert next_after(schedule, utc("2026-09-08T00:00:00")) == utc("2026-09-09T00:00:00")
        assert next_after(schedule, utc("2026-09-07T23:00:00")) == utc("2026-09-08T00:00:00")

    def test_the_same_instant_is_not_next(self) -> None:
        """엄격히 뒤여야 한다. 같으면 워커가 제자리를 맴돈다."""
        schedule = Schedule(cadence="daily", hour=9, minute=30, timezone=SEOUL)
        at = utc("2026-09-08T00:30:00")
        assert next_after(schedule, at) == utc("2026-09-09T00:30:00")

    def test_a_week_of_downtime_gives_the_next_one_not_seven(self) -> None:
        """**밀린 것을 몰아 만들지 않는다.**

        워커는 `지금` 을 넘긴다. 놓친 시각을 넘기면 그때부터 하나씩 따라잡으며
        같은 이슈를 여러 건 만든다 — 앱이 일주일 내려갔다 올라온 날 매일
        스케줄이 7건을 만드는 것은 복구가 아니라 알림 폭탄이다.
        """
        schedule = Schedule(cadence="daily", hour=9, minute=0, timezone=SEOUL)
        now = utc("2026-09-15T03:00:00")
        assert next_after(schedule, now) == utc("2026-09-16T00:00:00")


class TestWeekly:
    def test_it_finds_the_next_matching_weekday(self) -> None:
        # 2026-09-08 은 화요일. 다음 월요일은 9-14 이고, 서울 09:00 은 UTC 00:00.
        schedule = Schedule(cadence="weekly", hour=9, minute=0, timezone=SEOUL, weekday=0)
        assert next_after(schedule, utc("2026-09-08T00:00:00")) == utc("2026-09-14T00:00:00")

    def test_today_counts_when_the_time_has_not_passed(self) -> None:
        # 서울 기준 2026-09-14(월) 09:00 은 UTC 2026-09-14T00:00.
        schedule = Schedule(cadence="weekly", hour=9, minute=0, timezone=SEOUL, weekday=0)
        assert next_after(schedule, utc("2026-09-13T20:00:00")) == utc("2026-09-14T00:00:00")

    def test_today_is_skipped_when_the_time_has_passed(self) -> None:
        schedule = Schedule(cadence="weekly", hour=9, minute=0, timezone=SEOUL, weekday=0)
        assert next_after(schedule, utc("2026-09-14T01:00:00")) == utc("2026-09-21T00:00:00")


class TestMonthly:
    def test_it_fires_on_that_day(self) -> None:
        schedule = Schedule(cadence="monthly", hour=10, minute=0, timezone=SEOUL, day=15)
        assert next_after(schedule, utc("2026-09-08T00:00:00")) == utc("2026-09-15T01:00:00")

    def test_a_short_month_pulls_the_day_in(self) -> None:
        """**건너뛰지 않고 당긴다.** "매월 31일" 은 그 달의 마지막 날을 뜻한다."""
        schedule = Schedule(cadence="monthly", hour=10, minute=0, timezone=SEOUL, day=31)
        # 2026-02 는 28일까지다.
        assert next_after(schedule, utc("2026-02-01T00:00:00")) == utc("2026-02-28T01:00:00")

    def test_it_rolls_into_the_next_month(self) -> None:
        schedule = Schedule(cadence="monthly", hour=10, minute=0, timezone=SEOUL, day=1)
        assert next_after(schedule, utc("2026-09-01T02:00:00")) == utc("2026-10-01T01:00:00")

    def test_it_rolls_across_the_year(self) -> None:
        schedule = Schedule(cadence="monthly", hour=10, minute=0, timezone=SEOUL, day=1)
        assert next_after(schedule, utc("2026-12-01T02:00:00")) == utc("2027-01-01T01:00:00")


class TestDaylightSaving:
    def test_a_time_that_does_not_exist_is_pushed_forward(self) -> None:
        """**건너뛰지 않는다.**

        뉴욕은 2026-03-08 새벽 2시에서 3시로 뛴다. 그날 2:30 은 없는 시각인데,
        건너뛰면 매일 도는 스케줄이 그 하루만 조용히 안 돈다. 지금은 전환 전
        오프셋으로 해석되어 벽시계 3:30 에 돈다.
        """
        schedule = Schedule(cadence="daily", hour=2, minute=30, timezone=NEW_YORK)
        found = next_after(schedule, utc("2026-03-08T00:00:00"))
        assert found == utc("2026-03-08T07:30:00")
        assert found.astimezone(ZoneInfo(NEW_YORK)).hour == 3

    def test_a_time_that_happens_twice_fires_once(self) -> None:
        """뉴욕은 2026-11-01 에 1시가 두 번 온다. **앞의 것에 한 번** 돈다."""
        schedule = Schedule(cadence="daily", hour=1, minute=30, timezone=NEW_YORK)
        assert next_after(schedule, utc("2026-11-01T00:00:00")) == utc("2026-11-01T05:30:00")

    def test_the_local_hour_holds_across_the_change(self) -> None:
        """벽시계로 9시는 전환 뒤에도 9시다 — UTC 로는 한 시간 옮겨진다."""
        schedule = Schedule(cadence="daily", hour=9, minute=0, timezone=NEW_YORK)
        before = next_after(schedule, utc("2026-03-06T20:00:00"))
        after = next_after(schedule, utc("2026-03-10T20:00:00"))
        assert before == utc("2026-03-07T14:00:00")
        assert after == utc("2026-03-11T13:00:00")


class TestValidate:
    def test_it_accepts_the_three_cadences(self) -> None:
        validate(Schedule(cadence="daily", hour=0, minute=0, timezone="UTC"))
        validate(Schedule(cadence="weekly", hour=23, minute=59, timezone="UTC", weekday=6))
        validate(Schedule(cadence="monthly", hour=12, minute=0, timezone="UTC", day=31))

    @pytest.mark.parametrize(
        ("schedule", "code"),
        [
            (
                Schedule(cadence="hourly", hour=1, minute=0, timezone="UTC"),  # type: ignore[arg-type]
                "issues.recurrence_unknown_cadence",
            ),
            (
                Schedule(cadence="daily", hour=24, minute=0, timezone="UTC"),
                "issues.recurrence_bad_time",
            ),
            (
                Schedule(cadence="daily", hour=1, minute=60, timezone="UTC"),
                "issues.recurrence_bad_time",
            ),
            (
                Schedule(cadence="daily", hour=1, minute=0, timezone="Mars/Olympus"),
                "issues.recurrence_unknown_timezone",
            ),
            (
                Schedule(cadence="weekly", hour=1, minute=0, timezone="UTC"),
                "issues.recurrence_needs_weekday",
            ),
            (
                Schedule(cadence="weekly", hour=1, minute=0, timezone="UTC", weekday=7),
                "issues.recurrence_needs_weekday",
            ),
            (
                Schedule(cadence="monthly", hour=1, minute=0, timezone="UTC"),
                "issues.recurrence_needs_day",
            ),
            (
                Schedule(cadence="monthly", hour=1, minute=0, timezone="UTC", day=0),
                "issues.recurrence_needs_day",
            ),
            (
                Schedule(cadence="monthly", hour=1, minute=0, timezone="UTC", day=32),
                "issues.recurrence_needs_day",
            ),
        ],
    )
    def test_it_rejects_what_cannot_run(self, schedule: Schedule, code: str) -> None:
        with pytest.raises(ValidationError) as exc:
            validate(schedule)
        assert exc.value.code == code
