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


# ── 에스컬레이션 (C5) ───────────────────────────────────────────
#
# **등록된 이름 + 파라미터로만 저장한다.** 워크플로우 엔진과 같은 규약이다
# (module-guide) — 임의 코드를 저장하고 실행하는 길을 만들지 않는다.

#: 무엇을 할 수 있는가. 늘릴 때는 `validate_escalations` 의 검사도 늘린다.
ESCALATION_ACTIONS = ("notify", "raise_priority")

#: `at_percent` 의 상한. 목표의 다섯 배까지 — 그 뒤에 무언가 더 하는 것은
#: 자동화 규칙(C9)의 일이고, 여기서 무한정 받으면 오타가 그대로 저장된다.
MAX_ESCALATION_PERCENT = 500


@dataclass(frozen=True, slots=True)
class EscalationRule:
    """ "목표의 N% 를 썼을 때 이것을 한다".

    `at_percent` 로 적는 이유: 목표 시간은 우선순위·요청 유형마다 다르다.
    "3시간 남았을 때" 로 적으면 4시간 목표에서는 45분 만에, 3일 목표에서는
    거의 끝에 걸린다 — 관리자가 뜻한 것은 둘 중 하나뿐이다.

    100 이 목표 시각이다. 그보다 크면 위반 뒤의 조치다.
    """

    at_percent: int
    action: str
    #: `notify` 의 대상. 다른 액션에서는 `None`.
    user_id: UUID | None = None
    #: `raise_priority` 가 올릴 값. 다른 액션에서는 `None`.
    priority: int | None = None

    @property
    def key(self) -> str:
        """같은 규칙을 두 번 실행하지 않으려고 클럭에 적어 두는 이름.

        **번호(인덱스)가 아니라 내용으로 만든다.** 인덱스로 두면 관리자가
        규칙 순서를 바꾸는 순간 이미 실행한 것이 안 한 것으로 보인다 — 밤에
        두 번 호출되는 사람이 생긴다.
        """
        return f"{self.at_percent}:{self.action}"


def validate_escalations(rules: Any) -> list[dict[str, Any]]:
    """에스컬레이션 규칙을 검증해 그대로 돌려준다. 저장할 때 부른다.

    빈 목록은 정상이다 — 에스컬레이션 없는 정책이 대부분이다. 목표
    (`validate_goals`)와 달리 "기본 규칙" 을 요구하지 않는 이유가 그것이다.
    """
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise SlaError("에스컬레이션 규칙이 목록이 아니다")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise SlaError("에스컬레이션 규칙은 사전이어야 한다")
        unknown = set(rule) - {"at_percent", "action", "user_id", "priority"}
        if unknown:
            raise SlaError(f"모르는 항목: {sorted(unknown)}")
        percent = rule.get("at_percent")
        if not isinstance(percent, int) or isinstance(percent, bool):
            raise SlaError("at_percent 는 정수여야 한다")
        if not 1 <= percent <= MAX_ESCALATION_PERCENT:
            raise SlaError(f"at_percent 는 1..{MAX_ESCALATION_PERCENT} 여야 한다")
        action = rule.get("action")
        if action not in ESCALATION_ACTIONS:
            raise SlaError(f"모르는 조치: {action!r}")
        if action == "notify":
            if "user_id" not in rule:
                raise SlaError("notify 에는 user_id 가 필요하다")
            try:
                UUID(str(rule["user_id"]))
            except ValueError as exc:
                raise SlaError("user_id 가 UUID 가 아니다") from exc
            if "priority" in rule:
                raise SlaError("notify 는 priority 를 쓰지 않는다")
        if action == "raise_priority":
            value = rule.get("priority")
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise SlaError("raise_priority 의 priority 는 1..5 여야 한다")
            if "user_id" in rule:
                raise SlaError("raise_priority 는 user_id 를 쓰지 않는다")
        # **같은 이름의 규칙을 둘 두지 않는다.** 실행 표시가 이름으로
        # 남으므로, 이름이 겹치면 하나만 실행되고 나머지는 조용히 사라진다.
        parsed = parse_escalation(rule)
        if parsed.key in seen:
            raise SlaError(f"같은 조건·조치의 규칙이 둘 있다: {parsed.key}")
        seen.add(parsed.key)
        out.append(dict(rule))
    return out


def parse_escalation(rule: dict[str, Any]) -> EscalationRule:
    """저장된 사전을 규칙 객체로. 검증을 통과한 것만 넘긴다."""
    raw_user = rule.get("user_id")
    raw_priority = rule.get("priority")
    return EscalationRule(
        at_percent=int(rule["at_percent"]),
        action=str(rule["action"]),
        user_id=UUID(str(raw_user)) if raw_user is not None else None,
        priority=int(raw_priority) if raw_priority is not None else None,
    )


def consumed_percent(*, goal_seconds: int, remaining_seconds: int) -> int:
    """목표의 몇 %를 썼는가. 위반이면 100 을 넘는다.

    남은 시간에서 거꾸로 계산하는 이유: 남은 시간은 `remaining()` 이 이미
    달력으로 정확히 재고, 그 값이 화면에 뜨는 값이다. 여기서 따로 세면 화면의
    숫자와 에스컬레이션의 판단이 어긋날 수 있다 — 상담원은 "2시간 남았는데
    왜 에스컬레이션이 갔지" 를 묻게 된다.
    """
    if goal_seconds <= 0:
        # 목표가 0 이하인 클럭은 없다(`validate_goals` 가 양수를 요구한다).
        # 마이그레이션 전에 만들어진 행만 그럴 수 있어서, 나누지 않고 0 을
        # 돌려준다 — 그 클럭은 에스컬레이션 대상이 아니게 된다.
        return 0
    return round(100 * (goal_seconds - remaining_seconds) / goal_seconds)


def due_escalations(
    rules: list[dict[str, Any]], *, percent: int, already: list[str]
) -> list[EscalationRule]:
    """지금 실행할 규칙들. 이미 실행한 것은 빼고, **조건 순서대로** 돌려준다.

    한 스윕에서 여러 규칙이 함께 걸릴 수 있다: 워커가 한동안 멈춰 있었거나,
    목표가 아주 짧으면 75% 와 100% 를 같은 주기에 지나친다. 그때 **둘 다**
    실행한다 — 하나만 하고 나머지를 버리면 담당자를 올리는 규칙이 사라진다.
    """
    done = set(already)
    picked = [parse_escalation(rule) for rule in rules]
    return sorted(
        (rule for rule in picked if rule.at_percent <= percent and rule.key not in done),
        key=lambda rule: rule.at_percent,
    )


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
