"""SLA 정책 해석과 잔여 시간 (feature-map C4·C5).

`calendar.py` 가 시간을 세고 이 층이 **언제 세기 시작하고 멈추는지**를 정한다.
여기도 순수 함수라 값을 손으로 계산해 못 박는다.

붙잡는 것:

- 목표는 **위에서부터 처음 맞는 것**이 이긴다. 순서가 규칙이다.
- 조건 없는 기본 목표가 없는 정책은 **저장 전에 거절한다** — 없으면 조건에
  안 걸리는 티켓이 클럭 없이 굴러가고, 화면의 SLA 칸이 비어 있을 뿐이라
  아무도 그것이 빠졌다는 것을 모른다.
- 잔여 시간은 멈춰 있으면 **멈춘 시각을 기준으로** 잰다. 지금으로 재면 고객
  답변을 기다리는 티켓이 저절로 위반된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ieum.modules.desk.calendar import BusinessCalendar, CalendarError
from ieum.modules.desk.sla import (
    SlaError,
    TicketFacts,
    consumed_percent,
    due_escalations,
    goal_seconds,
    parse_calendar,
    parse_escalation,
    remaining,
    validate_escalations,
    validate_goals,
)

HOUR = 3600

SEOUL = BusinessCalendar(
    timezone="Asia/Seoul",
    week={day: ((time(9, 0), time(18, 0)),) for day in (0, 1, 2, 3, 4)},
)


def seoul(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul"))


def facts(priority: int = 3, request_type_id: object = None) -> TicketFacts:
    return TicketFacts(
        priority=priority,
        request_type_id=request_type_id,  # type: ignore[arg-type]
        organization_id=None,
    )


class TestReadingAStoredCalendar:
    def test_it_reads_the_stored_shape(self) -> None:
        calendar = parse_calendar(
            timezone="Asia/Seoul",
            working_hours={"0": [["09:00", "12:00"], ["13:00", "18:00"]]},
            holidays=["2026-09-08"],
        )
        assert calendar.spans_on(seoul("2026-09-07 00:00").date()) == (
            (time(9, 0), time(12, 0)),
            (time(13, 0), time(18, 0)),
        )
        # 휴일은 현지 날짜다.
        assert calendar.spans_on(seoul("2026-09-08 00:00").date()) == ()

    @pytest.mark.parametrize(
        "working_hours,holidays",
        [
            ("not a dict", []),
            ({"월": [["09:00", "18:00"]]}, []),
            ({"0": "not a list"}, []),
            ({"0": [["09:00"]]}, []),
            ({"0": [["9시", "18시"]]}, []),
            ({"0": [["09:00", "18:00"]]}, "not a list"),
            ({"0": [["09:00", "18:00"]]}, ["어제"]),
        ],
    )
    def test_a_broken_shape_is_refused_here_not_later(
        self, working_hours: object, holidays: object
    ) -> None:
        """**모양이 틀린 달력은 계산 단계까지 흘러가지 않는다.**

        흘러가면 SLA 가 조용히 이상한 숫자를 내고, 그건 며칠 뒤에 "SLA 가
        안 맞는다" 로만 보인다.
        """
        with pytest.raises(CalendarError):
            parse_calendar(timezone="Asia/Seoul", working_hours=working_hours, holidays=holidays)


class TestChoosingTheGoal:
    def test_the_first_match_wins(self) -> None:
        """순서가 규칙이다. 아래에 더 구체적인 것이 있어도 위가 이긴다."""
        goals = [
            {"priority_min": 4, "seconds": 1 * HOUR},
            {"seconds": 8 * HOUR},
        ]
        assert goal_seconds(goals, facts(priority=5)) == 1 * HOUR
        assert goal_seconds(goals, facts(priority=3)) == 8 * HOUR

    def test_order_is_the_rule_even_when_it_looks_wrong(self) -> None:
        """기본값을 맨 위에 적으면 **아래는 아무 것도 안 걸린다.**

        관리자가 자기가 적은 순서와 다른 결과를 보지 않게, "가장 구체적인
        것이 이긴다" 같은 암묵 규칙을 두지 않는다.
        """
        goals = [{"seconds": 8 * HOUR}, {"priority_min": 5, "seconds": 1 * HOUR}]
        assert goal_seconds(goals, facts(priority=5)) == 8 * HOUR

    def test_a_request_type_condition(self) -> None:
        wanted = uuid4()
        goals = [
            {"request_type_id": str(wanted), "seconds": 2 * HOUR},
            {"seconds": 8 * HOUR},
        ]
        assert goal_seconds(goals, facts(request_type_id=wanted)) == 2 * HOUR
        assert goal_seconds(goals, facts(request_type_id=uuid4())) == 8 * HOUR
        # 상담원이 대신 만든 티켓에는 요청 유형이 없다 — 조건이 안 걸린다.
        assert goal_seconds(goals, facts(request_type_id=None)) == 8 * HOUR

    def test_nothing_matches_gives_none(self) -> None:
        """`validate_goals` 가 이 상태를 막지만, 이미 저장된 정책은 있을 수
        있다 — 그때 예외가 아니라 `None` 이어야 한다. 클럭을 안 거는 것과
        워커가 죽는 것은 다르다."""
        assert goal_seconds([{"priority_min": 5, "seconds": HOUR}], facts(priority=1)) is None


class TestRefusingABrokenPolicy:
    def test_a_policy_with_no_default_goal_is_refused(self) -> None:
        """**이 시험이 이 클래스의 이유다.** 조건에 안 걸리는 티켓은 클럭 없이
        굴러가고, 화면에는 SLA 칸이 비어 있을 뿐이라 아무도 모른다."""
        with pytest.raises(SlaError):
            validate_goals([{"priority_min": 4, "seconds": HOUR}])

    def test_an_empty_goal_list_is_refused(self) -> None:
        with pytest.raises(SlaError):
            validate_goals([])

    @pytest.mark.parametrize(
        "goal",
        [
            {"seconds": 0},
            {"seconds": -1},
            {"seconds": "네 시간"},
            {"seconds": True},
            {"seconds": HOUR, "priority_min": "높음"},
            {"seconds": HOUR, "request_type_id": "not-a-uuid"},
            {"seconds": HOUR, "prioirty_min": 4},
        ],
    )
    def test_a_bad_goal_is_refused(self, goal: object) -> None:
        """오타 난 조건 이름(`prioirty_min`)도 거절한다 — 모르는 키를 조용히
        무시하면 그 조건은 **언제나 맞는** 목표가 된다."""
        with pytest.raises(SlaError):
            validate_goals([goal])

    def test_a_valid_policy_passes_through(self) -> None:
        goals = [{"priority_min": 4, "seconds": HOUR}, {"seconds": 8 * HOUR}]
        assert validate_goals(goals) == goals


class TestRemainingTime:
    def test_it_counts_working_time_not_wall_clock(self) -> None:
        """금요일 17:00 에 목표가 월요일 10:00 이면 남은 것은 **2시간**이다
        (금 17~18, 월 09~10). 벽시계로는 65시간이다."""
        left = remaining(
            SEOUL,
            target_at=seoul("2026-09-14 10:00"),
            now=seoul("2026-09-11 17:00"),
            paused_at=None,
            completed_at=None,
        )
        assert left.seconds == 2 * HOUR
        assert left.breached is False

    def test_a_paused_clock_is_measured_from_when_it_stopped(self) -> None:
        """**멈춰 있으면 지금으로 재지 않는다.**

        지금으로 재면 고객 답변을 기다리는 동안 잔여 시간이 계속 줄고, 그
        티켓은 아무도 잘못한 것 없이 저절로 위반된다.
        """
        left = remaining(
            SEOUL,
            target_at=seoul("2026-09-07 17:00"),
            now=seoul("2026-09-09 12:00"),  # 이틀 뒤
            paused_at=seoul("2026-09-07 15:00"),
            completed_at=None,
        )
        assert left.paused is True
        assert left.breached is False
        assert left.seconds == 2 * HOUR

    def test_a_breach_is_negative(self) -> None:
        """지난 만큼 음수로 준다 — 화면이 "3시간 초과" 를 말할 수 있어야 한다."""
        left = remaining(
            SEOUL,
            target_at=seoul("2026-09-07 10:00"),
            now=seoul("2026-09-07 13:00"),
            paused_at=None,
            completed_at=None,
        )
        assert left.breached is True
        assert left.seconds == -3 * HOUR

    def test_a_completed_clock_shows_nothing(self) -> None:
        """이미 지킨 약속에 남은 시간을 그리면, 끝난 티켓이 계속 재촉하는
        것처럼 보인다."""
        left = remaining(
            SEOUL,
            target_at=seoul("2026-09-07 10:00"),
            now=seoul("2026-09-09 10:00"),
            paused_at=None,
            completed_at=seoul("2026-09-07 09:30"),
        )
        assert left.completed is True
        assert left.breached is False

    def test_the_weekend_does_not_burn_the_clock(self) -> None:
        """금요일 17:30 에 30분 남은 티켓은 월요일 아침에도 30분 남았다."""
        target = seoul("2026-09-11 18:00")
        friday = remaining(
            SEOUL,
            target_at=target,
            now=seoul("2026-09-11 17:30"),
            paused_at=None,
            completed_at=None,
        )
        monday = remaining(
            SEOUL,
            target_at=target,
            now=seoul("2026-09-12 09:00"),
            paused_at=None,
            completed_at=None,
        )
        assert friday.seconds == 30 * 60
        # 토요일에는 업무 시간이 없으므로 목표를 이미 지났다 — 위반이지만
        # **주말만큼 초과된 것이 아니다.**
        assert monday.breached is True
        assert monday.seconds == 0

    def test_it_works_with_utc_input(self) -> None:
        """DB 는 UTC 로 준다. 타임존만 다른 같은 시각이면 답이 같아야 한다."""
        left = remaining(
            SEOUL,
            target_at=seoul("2026-09-07 17:00").astimezone(UTC),
            now=seoul("2026-09-07 15:00").astimezone(UTC),
            paused_at=None,
            completed_at=None,
        )
        assert left.seconds == 2 * HOUR


class TestRefusingABrokenEscalation:
    """**저장할 때 거절한다.** 실행되지 않는 규칙은 조용하다 — 밤에 아무도
    호출되지 않았다는 사실은 아침에야, 그것도 운이 좋으면 드러난다."""

    def test_an_empty_list_is_fine(self) -> None:
        """에스컬레이션 없는 정책이 대부분이다. 목표와 달리 기본 규칙을
        요구하지 않는다."""
        assert validate_escalations([]) == []
        assert validate_escalations(None) == []

    def test_a_good_rule_passes_through_unchanged(self) -> None:
        who = str(uuid4())
        rules = [
            {"at_percent": 75, "action": "notify", "user_id": who},
            {"at_percent": 100, "action": "raise_priority", "priority": 5},
        ]
        assert validate_escalations(rules) == rules

    @pytest.mark.parametrize(
        "rule",
        [
            {"at_percent": 0, "action": "notify", "user_id": str(uuid4())},  # 0% 는 접수 즉시다
            {"at_percent": 501, "action": "notify", "user_id": str(uuid4())},
            {"at_percent": "75", "action": "notify", "user_id": str(uuid4())},
            {"at_percent": True, "action": "notify", "user_id": str(uuid4())},  # bool 은 int 다
            {"at_percent": 75, "action": "delete_everything"},
            {"at_percent": 75, "action": "notify"},  # 누구에게?
            {"at_percent": 75, "action": "notify", "user_id": "not-a-uuid"},
            {"at_percent": 75, "action": "notify", "user_id": str(uuid4()), "priority": 5},
            {"at_percent": 75, "action": "raise_priority"},  # 몇으로?
            {"at_percent": 75, "action": "raise_priority", "priority": 0},
            {"at_percent": 75, "action": "raise_priority", "priority": 6},
            {"at_percent": 75, "action": "raise_priority", "priority": 5, "user_id": str(uuid4())},
            {"at_percent": 75, "action": "notify", "user_id": str(uuid4()), "extra": 1},
        ],
    )
    def test_a_rule_that_cannot_run_is_refused(self, rule: dict[str, object]) -> None:
        with pytest.raises(SlaError):
            validate_escalations([rule])

    def test_two_rules_with_the_same_name_are_refused(self) -> None:
        """실행 표시가 **내용으로 만든 이름**으로 남으므로, 이름이 겹치면
        하나만 실행되고 나머지는 조용히 사라진다."""
        who = str(uuid4())
        with pytest.raises(SlaError):
            validate_escalations(
                [
                    {"at_percent": 100, "action": "notify", "user_id": who},
                    {"at_percent": 100, "action": "notify", "user_id": str(uuid4())},
                ]
            )

    def test_the_same_percent_with_different_actions_is_fine(self) -> None:
        """목표 시각에 알리고 우선순위도 올리는 것은 흔한 설정이다."""
        rules = [
            {"at_percent": 100, "action": "notify", "user_id": str(uuid4())},
            {"at_percent": 100, "action": "raise_priority", "priority": 5},
        ]
        assert len(validate_escalations(rules)) == 2


class TestConsumedPercent:
    """**남은 시간에서 거꾸로 계산한다.** 화면에 뜨는 숫자와 같은 값을
    써야, 상담원이 "2시간 남았는데 왜 에스컬레이션이 갔지" 를 묻지 않는다."""

    @pytest.mark.parametrize(
        ("goal", "left", "percent"),
        [
            (4 * HOUR, 4 * HOUR, 0),  # 아직 안 썼다
            (4 * HOUR, 3 * HOUR, 25),
            (4 * HOUR, 1 * HOUR, 75),
            (4 * HOUR, 0, 100),  # 목표 시각
            (4 * HOUR, -2 * HOUR, 150),  # 위반. 100 을 넘는다
            (3 * HOUR, 1 * HOUR, 67),  # 66.67 → 반올림
        ],
    )
    def test_it_counts_from_what_is_left(self, goal: int, left: int, percent: int) -> None:
        assert consumed_percent(goal_seconds=goal, remaining_seconds=left) == percent

    def test_a_goalless_clock_is_never_escalated(self) -> None:
        """마이그레이션 전에 만들어진 행만 그렇다. 나누면 터지고, 100 으로
        읽으면 지난 티켓 전부에 에스컬레이션이 한꺼번에 나간다."""
        assert consumed_percent(goal_seconds=0, remaining_seconds=0) == 0


class TestPickingWhatToRun:
    def _rules(self) -> list[dict[str, object]]:
        return [
            {"at_percent": 50, "action": "notify", "user_id": str(uuid4())},
            {"at_percent": 100, "action": "raise_priority", "priority": 5},
        ]

    def test_nothing_runs_before_its_percent(self) -> None:
        assert due_escalations(self._rules(), percent=25, already=[]) == []

    def test_it_runs_what_the_clock_has_passed(self) -> None:
        due = due_escalations(self._rules(), percent=60, already=[])
        assert [rule.at_percent for rule in due] == [50]

    def test_it_runs_everything_passed_at_once(self) -> None:
        """워커가 한동안 멈춰 있었거나 목표가 아주 짧으면 두 조건을 같은
        주기에 지나친다. 하나만 하고 나머지를 버리면 우선순위를 올리는
        규칙이 사라진다."""
        due = due_escalations(self._rules(), percent=120, already=[])
        assert [rule.at_percent for rule in due] == [50, 100]

    def test_already_run_rules_do_not_run_again(self) -> None:
        """스윕은 15초마다 돈다. 표시를 안 보면 담당자는 15초마다 호출된다."""
        rules = self._rules()
        first = due_escalations(rules, percent=120, already=[])
        keys = [rule.key for rule in first]
        assert due_escalations(rules, percent=120, already=keys) == []

    def test_it_runs_in_condition_order(self) -> None:
        """뒤에 적은 규칙이 낮은 %면 그것이 먼저다 — 순서는 조건이 정하고,
        적은 순서가 정하지 않는다."""
        rules = [
            {"at_percent": 100, "action": "raise_priority", "priority": 5},
            {"at_percent": 50, "action": "notify", "user_id": str(uuid4())},
        ]
        assert [rule.at_percent for rule in due_escalations(rules, percent=150, already=[])] == [
            50,
            100,
        ]

    def test_the_name_does_not_move_when_rules_are_reordered(self) -> None:
        """**번호가 아니라 내용으로** 이름을 만든다. 번호로 두면 관리자가
        순서를 바꾸는 순간 이미 실행한 것이 안 한 것으로 보인다."""
        who = str(uuid4())
        a = parse_escalation({"at_percent": 75, "action": "notify", "user_id": who})
        b = parse_escalation({"at_percent": 75, "action": "notify", "user_id": str(uuid4())})
        # 대상이 달라도 조건·조치가 같으면 같은 규칙이다 — 그래서 저장할 때
        # 거절한다(위 시험).
        assert a.key == b.key == "75:notify"
