"""업무 시간 계산 (feature-map C4).

**SLA 의 모든 숫자가 이 모듈에 걸려 있다.** 그래서 값을 손으로 계산해 못
박는다 — 함수가 스스로를 검증하게 두면(두 함수를 서로 돌려 비교하는 식으로)
둘이 같이 틀린 상태를 통과시킨다.

붙잡는 것:

- 업무 시간 밖의 시간은 **세지 않는다.** 금요일 오후에 들어온 요청이 월요일
  아침에 이미 위반이면 그건 벽시계로 잰 것이다.
- `add_working_seconds` 와 `working_seconds_between` 이 **서로의 역**이다.
- 여름 시간(DST)에 하루가 23·25시간이 되어도 업무 초는 그대로다.
- 성립하지 않는 달력은 저장 전에 거절한다 — 업무 시간이 하루도 없는 달력에
  걸린 티켓은 조용히 SLA 없이 굴러간다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from ieum.modules.desk.calendar import (
    END_OF_DAY,
    BusinessCalendar,
    CalendarError,
    add_working_seconds,
    working_seconds_between,
)

HOUR = 3600

#: 월~금 09:00~18:00, 점심 12:00~13:00 을 뺀다. 하루 8시간이다.
SEOUL = BusinessCalendar(
    timezone="Asia/Seoul",
    week={day: ((time(9, 0), time(12, 0)), (time(13, 0), time(18, 0))) for day in (0, 1, 2, 3, 4)},
)


def seoul(text: str) -> datetime:
    """`2026-09-07 10:00` 을 서울 시각으로 읽어 aware datetime 으로."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul"))


class TestCountingWorkingTime:
    def test_within_one_span(self) -> None:
        # 2026-09-07 은 월요일. 10:00~11:30 은 1.5시간.
        assert working_seconds_between(
            SEOUL, seoul("2026-09-07 10:00"), seoul("2026-09-07 11:30")
        ) == int(1.5 * HOUR)

    def test_lunch_is_not_counted(self) -> None:
        """11:00~14:00 은 세 시간이지만 업무는 두 시간이다."""
        assert (
            working_seconds_between(SEOUL, seoul("2026-09-07 11:00"), seoul("2026-09-07 14:00"))
            == 2 * HOUR
        )

    def test_a_whole_day_is_eight_hours(self) -> None:
        assert (
            working_seconds_between(SEOUL, seoul("2026-09-07 00:00"), seoul("2026-09-08 00:00"))
            == 8 * HOUR
        )

    def test_the_weekend_is_not_counted(self) -> None:
        """**이 시험이 이 파일의 이유다.** 금요일 17:00 에 들어온 요청.

        벽시계로 재면 월요일 09:00 까지 64시간이다. 업무로는 1시간(금요일
        17:00~18:00)뿐이다. "4시간 안에 응답" 이 월요일 아침에 이미 위반이면
        그건 아무도 일하지 않은 시간을 센 것이다.
        """
        # 2026-09-11 은 금요일, 2026-09-14 는 월요일.
        counted = working_seconds_between(
            SEOUL, seoul("2026-09-11 17:00"), seoul("2026-09-14 09:00")
        )
        assert counted == 1 * HOUR

    def test_a_holiday_is_not_counted(self) -> None:
        """휴일은 **현지 날짜**로 본다. UTC 로 두면 시차만큼 어긋난 날이 쉰다."""
        with_holiday = BusinessCalendar(
            timezone=SEOUL.timezone,
            week=SEOUL.week,
            holidays=frozenset({date(2026, 9, 8)}),
        )
        # 월요일 09:00 → 화요일(휴일) → 수요일 09:00. 업무는 월요일 8시간뿐.
        assert (
            working_seconds_between(
                with_holiday, seoul("2026-09-07 09:00"), seoul("2026-09-09 09:00")
            )
            == 8 * HOUR
        )

    def test_time_outside_business_hours_counts_as_zero(self) -> None:
        """토요일 내내 기다려도 업무 초는 0 이다."""
        assert (
            working_seconds_between(SEOUL, seoul("2026-09-12 00:00"), seoul("2026-09-13 23:59"))
            == 0
        )

    def test_a_backwards_range_is_zero_not_negative(self) -> None:
        """음수를 돌려주면 부르는 쪽이 그것을 "남은 시간" 으로 더해 버린다."""
        assert (
            working_seconds_between(SEOUL, seoul("2026-09-07 14:00"), seoul("2026-09-07 10:00"))
            == 0
        )

    def test_it_refuses_a_naive_datetime(self) -> None:
        """naive 를 UTC 로 가정하면 어딘가에서 현지 시각을 그대로 넘긴 순간
        조용히 시차만큼 틀린 답이 나온다."""
        with pytest.raises(CalendarError):
            working_seconds_between(
                SEOUL,
                datetime(2026, 9, 7, 10, 0),
                seoul("2026-09-07 11:00"),
            )


class TestFindingTheTargetTime:
    def test_within_one_span(self) -> None:
        assert add_working_seconds(SEOUL, seoul("2026-09-07 10:00"), 2 * HOUR) == seoul(
            "2026-09-07 12:00"
        ).astimezone(UTC)

    def test_it_jumps_over_lunch(self) -> None:
        """11:00 + 2시간 = 14:00 이다. 13:00 이 아니다."""
        assert add_working_seconds(SEOUL, seoul("2026-09-07 11:00"), 2 * HOUR) == seoul(
            "2026-09-07 14:00"
        ).astimezone(UTC)

    def test_it_jumps_over_the_weekend(self) -> None:
        """금요일 17:00 + 4시간 = 월요일 12:00.

        금요일에 1시간(17~18), 월요일에 3시간(09~12).
        """
        assert add_working_seconds(SEOUL, seoul("2026-09-11 17:00"), 4 * HOUR) == seoul(
            "2026-09-14 12:00"
        ).astimezone(UTC)

    def test_a_request_outside_business_hours_starts_at_the_next_opening(self) -> None:
        """**토요일에 들어온 요청의 4시간은 월요일 14:00 까지다.**

        시작을 그대로 두면 목표 시각이 업무 시간 밖에 놓이고, 접수 즉시
        위반인 티켓이 된다.

        월요일 오전은 09:00~12:00 로 3시간뿐이므로 나머지 1시간은 점심 뒤로
        넘어간다 — 처음에 13:00 이라고 적었다가 틀렸다. 손으로 계산하는 값을
        못 박는 이유가 이것인데, 그러려면 계산이 맞아야 한다.
        """
        assert add_working_seconds(SEOUL, seoul("2026-09-12 15:00"), 4 * HOUR) == seoul(
            "2026-09-14 14:00"
        ).astimezone(UTC)

    def test_zero_seconds_gives_the_next_business_moment(self) -> None:
        """0 이면 **다음 업무 시각**이다. 시작 시각을 그대로 주면 업무 시간
        밖의 목표가 생긴다."""
        assert add_working_seconds(SEOUL, seoul("2026-09-12 15:00"), 0) == seoul(
            "2026-09-14 09:00"
        ).astimezone(UTC)
        # 업무 시간 안이면 그 시각 그대로다.
        assert add_working_seconds(SEOUL, seoul("2026-09-07 10:00"), 0) == seoul(
            "2026-09-07 10:00"
        ).astimezone(UTC)

    def test_it_refuses_negative_seconds(self) -> None:
        with pytest.raises(CalendarError):
            add_working_seconds(SEOUL, seoul("2026-09-07 10:00"), -1)

    @pytest.mark.parametrize(
        "start,seconds",
        [
            ("2026-09-07 09:00", 1 * HOUR),
            ("2026-09-07 11:30", 3 * HOUR),
            ("2026-09-11 17:30", 12 * HOUR),
            ("2026-09-12 08:00", 40 * HOUR),
            ("2026-09-07 09:00", 8 * HOUR),
        ],
    )
    def test_the_two_functions_are_inverses(self, start: str, seconds: int) -> None:
        """`add` 로 잡은 목표까지 `between` 으로 재면 그 초가 나와야 한다.

        이것만으로는 부족해서 위의 값들을 손으로 적어 두었다 — 둘이 같은
        방향으로 틀리면 이 시험은 통과한다.
        """
        target = add_working_seconds(SEOUL, seoul(start), seconds)
        assert working_seconds_between(SEOUL, seoul(start), target) == seconds


class TestDaylightSaving:
    """여름 시간에 하루가 23·25시간이 되어도 **업무 초는 그대로다.**

    UTC 오프셋으로 계산하면 전환하는 주의 SLA 가 한 시간씩 틀린다. 한국에는
    DST 가 없으므로 있는 곳으로 시험한다.
    """

    BERLIN = BusinessCalendar(
        timezone="Europe/Berlin",
        week={day: ((time(9, 0), time(17, 0)),) for day in (0, 1, 2, 3, 4)},
    )

    def berlin(self, text: str) -> datetime:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Berlin"))

    def test_the_spring_forward_day_still_has_eight_working_hours(self) -> None:
        # 2026-03-29 (일) 새벽에 한 시간이 사라진다. 그 주 월요일은 평범한
        # 8시간이어야 한다.
        assert (
            working_seconds_between(
                self.BERLIN, self.berlin("2026-03-30 00:00"), self.berlin("2026-03-31 00:00")
            )
            == 8 * HOUR
        )

    def test_the_target_across_the_transition_is_still_eight_working_hours(self) -> None:
        """금요일 09:00 + 16시간 = 다음 월요일 17:00 — 그 사이에 전환이 있다."""
        target = add_working_seconds(self.BERLIN, self.berlin("2026-03-27 09:00"), 16 * HOUR)
        assert target == self.berlin("2026-03-30 17:00").astimezone(UTC)


class TestDaylightSavingInAZoneThatJumpsAtNight:
    """**여름 시간이 시작·끝나는 날의 시간을 벽시계로 셌다.**

    파이썬은 tzinfo 가 같은 두 aware 시각을 벽시계로 뺀다. 업무 구간을 현지
    시각으로 만들어 빼고 더했더니, 뉴욕에서 02:00 이 03:00 으로 건너뛰는 날의
    24시간 창구가 24시간으로 세어졌고(실제로는 23시간), 목표 시각은 사라진 한
    시간만큼 앞당겨졌다. 위의 베를린 시험은 업무 시간(09~17시)이 전환 시각을
    비껴가서 이것을 잡지 못했다.

    2026년 뉴욕: 3월 8일(일) 02:00 → 03:00, 11월 1일(일) 02:00 → 01:00.
    """

    ALWAYS = BusinessCalendar(
        timezone="America/New_York",
        week={day: ((time(0, 0), END_OF_DAY),) for day in range(7)},
    )
    #: 일요일 새벽 01:00~04:00 만 연다. 전환이 구간 한가운데에 있다.
    NIGHT = BusinessCalendar(
        timezone="America/New_York",
        week={6: ((time(1, 0), time(4, 0)),)},
    )

    def new_york(self, text: str) -> datetime:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo("America/New_York")
        )

    def test_the_spring_forward_day_has_twenty_three_hours(self) -> None:
        assert (
            working_seconds_between(
                self.ALWAYS, self.new_york("2026-03-08 00:00"), self.new_york("2026-03-09 00:00")
            )
            == 23 * HOUR
        )

    def test_the_fall_back_day_has_twenty_five_hours(self) -> None:
        assert (
            working_seconds_between(
                self.ALWAYS, self.new_york("2026-11-01 00:00"), self.new_york("2026-11-02 00:00")
            )
            == 25 * HOUR
        )

    def test_a_window_across_the_jump_is_two_real_hours(self) -> None:
        """01:00 EST ~ 04:00 EDT 는 벽시계로 세 시간, 실제로는 두 시간이다."""
        assert (
            working_seconds_between(
                self.NIGHT, self.new_york("2026-03-08 00:00"), self.new_york("2026-03-09 00:00")
            )
            == 2 * HOUR
        )

    def test_the_target_counts_real_hours_across_the_jump(self) -> None:
        """자정(EST, 05:00 UTC)에서 업무 세 시간이면 08:00 UTC — 현지로는
        04:00 EDT 다. 벽시계로 더하면 03:00 EDT(07:00 UTC)가 나와, 약속이 실제로
        두 시간이 된다."""
        target = add_working_seconds(self.ALWAYS, self.new_york("2026-03-08 00:00"), 3 * HOUR)
        assert target == datetime(2026, 3, 8, 8, 0, tzinfo=UTC)

    def test_the_target_inside_a_window_across_the_jump(self) -> None:
        """구간의 두 시간을 다 쓰면 구간의 끝(04:00 EDT = 08:00 UTC)이다.
        한 시간을 더 쓰면 다음 주 일요일 02:00 EST(07:00 UTC)다."""
        start = self.new_york("2026-03-08 01:00")
        assert add_working_seconds(self.NIGHT, start, 2 * HOUR) == datetime(
            2026, 3, 8, 8, 0, tzinfo=UTC
        )
        assert add_working_seconds(self.NIGHT, start, 3 * HOUR) == datetime(
            2026, 3, 15, 6, 0, tzinfo=UTC
        )

    def test_the_two_functions_stay_inverses_across_the_jump(self) -> None:
        start = self.new_york("2026-03-07 22:00")
        target = add_working_seconds(self.ALWAYS, start, 10 * HOUR)
        assert working_seconds_between(self.ALWAYS, start, target) == 10 * HOUR


class TestTheEndOfTheDay:
    """`24:00` 은 **다음 날 0시**다. `23:59` 로 적게 두면 24시간 창구가 매일
    1분씩 문을 닫는다."""

    ALWAYS = BusinessCalendar(
        timezone="Asia/Seoul",
        week={day: ((time(0, 0), END_OF_DAY),) for day in range(7)},
    )

    def test_a_whole_day_is_twenty_four_hours(self) -> None:
        assert (
            working_seconds_between(
                self.ALWAYS, seoul("2026-09-07 00:00"), seoul("2026-09-08 00:00")
            )
            == 24 * HOUR
        )

    def test_midnight_is_not_a_gap(self) -> None:
        """자정을 넘는 두 시간도 두 시간이다."""
        assert (
            working_seconds_between(
                self.ALWAYS, seoul("2026-09-07 23:00"), seoul("2026-09-08 01:00")
            )
            == 2 * HOUR
        )
        assert add_working_seconds(self.ALWAYS, seoul("2026-09-07 23:00"), 2 * HOUR) == seoul(
            "2026-09-08 01:00"
        ).astimezone(UTC)

    def test_an_evening_span_can_run_to_midnight(self) -> None:
        evening = BusinessCalendar(timezone="Asia/Seoul", week={0: ((time(18, 0), END_OF_DAY),)})
        # 2026-09-07 은 월요일.
        assert (
            working_seconds_between(evening, seoul("2026-09-07 00:00"), seoul("2026-09-09 00:00"))
            == 6 * HOUR
        )

    def test_nothing_can_follow_the_end_of_the_day(self) -> None:
        with pytest.raises(CalendarError):
            BusinessCalendar(
                timezone="Asia/Seoul",
                week={0: ((time(9, 0), END_OF_DAY), (time(23, 0), time(23, 30)))},
            )


class TestRefusingABrokenCalendar:
    def test_a_calendar_with_no_working_time_is_refused(self) -> None:
        """**업무 시간이 하루도 없으면 클럭이 영원히 멈춘다.** 목표 시각도
        못 잡고, 그 정책에 걸린 티켓 전부가 조용히 SLA 없이 굴러간다."""
        with pytest.raises(CalendarError):
            BusinessCalendar(timezone="Asia/Seoul", week={})

    def test_overlapping_spans_are_refused(self) -> None:
        """겹치면 그 시간이 두 번 세어진다."""
        with pytest.raises(CalendarError):
            BusinessCalendar(
                timezone="Asia/Seoul",
                week={0: ((time(9, 0), time(13, 0)), (time(12, 0), time(18, 0)))},
            )

    def test_a_backwards_span_is_refused(self) -> None:
        with pytest.raises(CalendarError):
            BusinessCalendar(timezone="Asia/Seoul", week={0: ((time(18, 0), time(9, 0)),)})

    def test_an_unknown_timezone_is_refused(self) -> None:
        with pytest.raises(CalendarError):
            BusinessCalendar(timezone="Mars/Olympus", week={0: ((time(9, 0), time(18, 0)),)})

    def test_a_bad_weekday_is_refused(self) -> None:
        with pytest.raises(CalendarError):
            BusinessCalendar(timezone="Asia/Seoul", week={7: ((time(9, 0), time(18, 0)),)})
