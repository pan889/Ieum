"""SLA 클럭을 움직인다 (feature-map C4·C5).

**워커가 움직인다.** API 요청 경로에서 재면 아무도 열어 보지 않은 티켓은
영원히 위반이 아니게 되고, 그건 SLA 를 안 재는 것과 같다 (overview.md 3절:
알림·색인·SLA 계산·웹훅은 전부 비동기).

`calendar.py` 가 시간을 세고 `sla.py` 가 목표를 고르고, 여기가 **행을 쓴다.**
세 층을 나눈 이유는 앞의 둘이 순수 함수라 손으로 계산해 시험할 수 있기
때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.events import EventEnvelope
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.desk import sla
from ieum.modules.desk.calendar import BusinessCalendar, CalendarError, add_working_seconds
from ieum.modules.desk.models import BusinessCalendarRow, SlaClock, SlaPolicy, TicketExt
from ieum.modules.issues import contracts as issues

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClockContext:
    """워커가 이벤트마다 넘기는 것. `notify` 의 핸들러와 같은 모양이다."""

    session: AsyncSession


async def handle_desk_event(ctx: ClockContext, envelope: EventEnvelope) -> int:
    """데스크가 듣는 이벤트를 처리한다. 움직인 클럭 수를 돌려준다.

    아웃박스의 **모든** 이벤트가 여기로 오므로, 관심 없는 것은 곧바로
    돌려보낸다 — 이벤트 종류마다 조회를 한 번씩 하면 드레인이 느려진다.
    """
    if envelope.event_type == "desk.ticket.submitted":
        return await start_clocks(ctx.session, envelope.aggregate_id)
    if envelope.event_type == "issue.commented":
        return await on_comment(
            ctx.session,
            envelope.aggregate_id,
            actor_id=envelope.uuid("actor_id"),
            is_internal=bool(envelope.payload.get("is_internal")),
        )
    if envelope.event_type == "issue.transitioned":
        return await on_transition(
            ctx.session,
            envelope.aggregate_id,
            to_state_category=str(envelope.payload.get("to_state_category") or ""),
        )
    return 0


async def start_clocks(session: AsyncSession, issue_id: UUID) -> int:
    """이 티켓에 걸리는 모든 정책의 클럭을 만든다.

    **`started_at` 은 이슈가 만들어진 시각이다.** 지금으로 두면 아웃박스가
    늦게 훑은 만큼 공짜 시간이 생긴다 — 드레인이 5분 밀린 날에는 모든 티켓이
    5분씩 유리해진다.
    """
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        return 0
    issue = await issues.get_issue(session, issue_id)
    if issue is None:
        return 0

    facts = sla.TicketFacts(
        priority=await _priority_of(session, issue_id),
        request_type_id=ticket.request_type_id,
        organization_id=ticket.organization_id,
    )
    policies = list(
        (
            await session.execute(
                select(SlaPolicy).where(
                    SlaPolicy.project_id == issue.project_id,
                    SlaPolicy.is_enabled.is_(True),
                    SlaPolicy.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not policies:
        return 0

    started = sla.utc(await _created_at_of(session, issue_id))
    made = 0
    for policy in policies:
        seconds = sla.goal_seconds(list(policy.goals), facts)
        if seconds is None:
            # 목표가 안 맞는다. `validate_goals` 가 기본 목표를 요구하므로
            # 새 정책에서는 일어나지 않지만, 이미 저장된 정책은 있을 수 있다.
            # **조용히 넘긴다 — 워커가 죽으면 다른 티켓의 클럭도 안 걸린다.**
            log.warning("sla.no_matching_goal", policy_id=str(policy.id), issue_id=str(issue_id))
            continue
        if await session.get(SlaClock, (issue_id, policy.id)) is not None:
            # 이미 있다. 아웃박스가 같은 이벤트를 두 번 주더라도 목표가
            # 다시 계산되어서는 안 된다.
            continue
        calendar = await _calendar_of(session, policy)
        if calendar is None:
            continue
        session.add(
            SlaClock(
                issue_id=issue_id,
                policy_id=policy.id,
                started_at=started,
                target_at=add_working_seconds(calendar, started, seconds),
                # **약속의 크기를 적어 둔다.** `target_at` 에서 거꾸로 계산
                # 하려면 달력이 필요하고, 멈춤으로 목표가 밀린 뒤에는 원래
                # 약속이 얼마였는지 알 수 없게 된다 — 에스컬레이션이 "목표의
                # 몇 %" 를 재려면 이 값이 있어야 한다.
                goal_seconds=seconds,
            )
        )
        made += 1
    await session.flush()
    return made


async def on_comment(
    session: AsyncSession, issue_id: UUID, *, actor_id: UUID | None, is_internal: bool
) -> int:
    """공개 회신이 나가면 **첫 응답** 시계가 끝난다.

    내부 노트는 응답이 아니다. 고객은 그것을 볼 수 없으므로, 내부 노트로
    첫 응답 SLA 를 지킬 수 있게 두면 그 지표는 아무 것도 뜻하지 않는다.

    고객 자신의 회신도 응답이 아니다 — 고객이 한 번 더 물어서 우리 SLA 가
    지켜지는 것은 거꾸로다.
    """
    if is_internal:
        return 0
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        return 0
    if actor_id is not None and actor_id == ticket.reporter_customer_id:
        return 0
    return await _complete(session, issue_id, metric="first_response")


async def on_transition(session: AsyncSession, issue_id: UUID, *, to_state_category: str) -> int:
    """상태가 바뀌었다. 해결 시계를 끝내거나, 멈추거나, 다시 돌린다."""
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        return 0
    moved = 0
    if to_state_category == "done":
        moved += await _complete(session, issue_id, metric="resolution")
    state_id = await _state_id_of(session, issue_id)
    for clock, policy in await _clocks_of(session, issue_id):
        if clock.completed_at is not None:
            continue
        should_pause = state_id is not None and state_id in set(policy.pause_state_ids)
        if should_pause and clock.paused_at is None:
            clock.paused_at = utcnow()
            moved += 1
        elif not should_pause and clock.paused_at is not None:
            # **다시 돌 때 목표를 그만큼 미룬다.**
            #
            # `remaining()` 은 `target_at` 하나만 보고 잰다(`paused_seconds` 를
            # 받지 않는다). 그래서 목표를 안 미루면 기다린 시간이 그대로
            # 깎인 셈이 되고, 고객이 사흘 뒤에 답한 티켓은 답하자마자
            # 위반이다.
            #
            # 모델의 "목표는 한 번 정하고 그대로 둔다" 는 **설정 변경**을
            # 두고 한 말이다: 관리자가 달력이나 목표 시간을 고쳤다고 지난
            # 티켓의 판정이 바뀌면 안 된다. 이 티켓에서 실제로 일어난 일
            # (고객을 기다렸다)로 목표가 움직이는 것은 그것과 다르고, 화면에
            # 그 사실이 보인다.
            #
            # 계산은 **한 번만** 한다 — 처음에는 같은 식을 두 번 불러
            # `paused_seconds` 와 `target_at` 에 각각 넣었다. 값이 같으니
            # 결과는 맞았지만, 읽는 사람은 둘 중 무엇이 계산에 쓰이는지 알
            # 수 없다.
            calendar = await _calendar_of(session, policy)
            if calendar is not None:
                waited = sla.working_seconds(calendar, sla.utc(clock.paused_at), utcnow())
                clock.paused_seconds += waited
                clock.target_at = add_working_seconds(calendar, sla.utc(clock.target_at), waited)
            clock.paused_at = None
            moved += 1
    await session.flush()
    return moved


async def sweep_breaches(session: AsyncSession, *, limit: int = 200) -> list[SlaClock]:
    """목표를 넘긴 클럭을 찾아 **알렸다고 표시**하고 돌려준다.

    `breached_at` 은 "위반인가" 가 아니라 "알렸는가" 다. 위반 여부는
    `target_at` 과 지금을 비교하면 언제든 알 수 있고, 이 컬럼은 알림을 두 번
    보내지 않으려고 남긴다.

    멈춰 있는 클럭은 건드리지 않는다 — 고객 답변을 기다리는 티켓이 저절로
    위반되면 그건 아무도 잘못하지 않은 위반이다.
    """
    now = utcnow()
    rows = list(
        (
            await session.execute(
                select(SlaClock)
                .where(
                    SlaClock.completed_at.is_(None),
                    SlaClock.breached_at.is_(None),
                    SlaClock.paused_at.is_(None),
                    SlaClock.target_at <= now,
                )
                .order_by(SlaClock.target_at)
                .limit(limit)
                # **행을 잠근다.** 워커를 둘 이상 띄우면(HA 가이드가 그래도
                # 된다고 적어 둔 구성이다) 두 스윕이 같은 클럭을 같이 집어
                # 둘 다 `breached_at` 을 적고 둘 다 돌려준다 — 위반 알림이
                # 두 통 가고 에스컬레이션이 두 번 돈다. 아웃박스는 처음부터
                # 이렇게 잠그는데(`fetch_unpublished`) 스윕만 빠져 있었다.
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.breached_at = now
    await session.flush()
    return rows


@dataclass(frozen=True, slots=True)
class Escalation:
    """실행한 규칙 하나. 워커가 이것을 보고 이벤트를 낸다.

    `clock.py` 가 직접 발행하지 않는 이유는 `sweep_breaches` 와 같다: 이벤트에
    담을 이슈 키·프로젝트 이름은 다른 모듈에서 와야 하고, 그 조회를 여기
    넣으면 이 층이 세 모듈을 알게 된다.
    """

    clock: SlaClock
    rule: sla.EscalationRule
    #: 실제로 몇 %에서 돌았는가. 규칙의 조건이 아니다 — 워커가 밀렸으면
    #: 75% 규칙이 140% 에서 돈다.
    at_percent: int


async def sweep_escalations(session: AsyncSession, *, limit: int = 200) -> list[Escalation]:
    """조건을 지난 에스컬레이션 규칙을 실행한다.

    **스윕이다.** 위반과 같은 이유로 이벤트로는 알 수 없다 — "목표의 75% 를
    썼다" 는 아무 일도 일어나지 않아서 생기는 사실이다.

    멈춘 클럭은 건드리지 않는다. 고객 답변을 기다리는 동안 %가 오르면, 우리가
    안 한 일이 아닌데 담당자가 호출된다.

    **끝난 클럭도 건드리지 않는다.** 이미 응답한 티켓에 "응답이 늦습니다" 가
    가면 그 알림은 다음부터 무시된다.
    """
    now = utcnow()
    rows = (
        await session.execute(
            select(SlaClock, SlaPolicy)
            .join(SlaPolicy, SlaPolicy.id == SlaClock.policy_id)
            .where(
                SlaClock.completed_at.is_(None),
                SlaClock.paused_at.is_(None),
                # 규칙이 없는 정책은 아예 안 집는다. 티켓이 쌓이면 이 조건이
                # 없는 스윕은 클럭 전체를 읽고 달력을 파싱한다.
                SlaPolicy.escalations != cast("[]", JSONB),
            )
            .order_by(SlaClock.target_at)
            .limit(limit)
            # 위반 스윕과 같은 이유. **클럭만 잠근다** — 정책은 여러 클럭이
            # 함께 보는 행이라 같이 잠그면 스윕끼리 서로를 막는다.
            .with_for_update(of=SlaClock, skip_locked=True)
        )
    ).all()

    done: list[Escalation] = []
    for clock, policy in rows:
        calendar = await _calendar_of(session, policy)
        if calendar is None:
            continue
        left = sla.remaining(
            calendar,
            target_at=sla.utc(clock.target_at),
            now=now,
            paused_at=None,
            completed_at=None,
        )
        percent = sla.consumed_percent(
            goal_seconds=clock.goal_seconds, remaining_seconds=left.seconds
        )
        due = sla.due_escalations(
            list(policy.escalations), percent=percent, already=list(clock.escalated)
        )
        if not due:
            continue
        for rule in due:
            if rule.action == "raise_priority" and rule.priority is not None:
                await issues.raise_priority(session, clock.issue_id, to=rule.priority)
            done.append(Escalation(clock=clock, rule=rule, at_percent=percent))
        # **표시를 먼저 확실히 한다.** 같은 트랜잭션에서 커밋되므로 이벤트와
        # 함께 남거나 함께 사라진다 — 표시만 남고 알림이 사라지는 반쪽은
        # 생기지 않는다.
        #
        # 새 목록을 **대입한다.** `ARRAY` 컬럼은 제자리 변경을 추적하지
        # 않으므로 `list.append` 는 저장되지 않는다 — 되돌려 확인했다:
        # append 로 바꾸면 "한 번만 실행된다" 시험이 붉어지고, 규칙이 스윕마다
        # 다시 돈다.
        clock.escalated = [*clock.escalated, *(rule.key for rule in due)]
    await session.flush()
    return done


# ── 내부 ────────────────────────────────────────────────────────


async def _complete(session: AsyncSession, issue_id: UUID, *, metric: str) -> int:
    """이 지표의 안 끝난 클럭을 지금으로 끝낸다.

    **이미 끝난 것은 다시 안 건드린다.** 두 번째 회신으로 첫 응답 시각이
    갱신되면 그 지표는 "마지막 응답" 이 된다.
    """
    done = 0
    for clock, policy in await _clocks_of(session, issue_id):
        if policy.metric != metric or clock.completed_at is not None:
            continue
        clock.completed_at = utcnow()
        done += 1
    await session.flush()
    return done


async def _clocks_of(session: AsyncSession, issue_id: UUID) -> list[tuple[SlaClock, SlaPolicy]]:
    rows = await session.execute(
        select(SlaClock, SlaPolicy)
        .join(SlaPolicy, SlaPolicy.id == SlaClock.policy_id)
        .where(SlaClock.issue_id == issue_id)
    )
    return [(clock, policy) for clock, policy in rows.all()]


async def _calendar_of(session: AsyncSession, policy: SlaPolicy) -> BusinessCalendar | None:
    row = await session.get(BusinessCalendarRow, policy.calendar_id)
    if row is None:
        return None
    try:
        return sla.parse_calendar(
            timezone=row.timezone, working_hours=row.working_hours, holidays=list(row.holidays)
        )
    except CalendarError as exc:
        # **워커를 죽이지 않는다.** 달력 하나가 망가졌다고 다른 프로젝트의
        # 클럭까지 멈추면 피해가 번진다. 저장할 때 검증하므로 여기 오는 것은
        # 손으로 고친 행이다.
        log.error("sla.broken_calendar", calendar_id=str(row.id), error=str(exc))
        return None


async def _priority_of(session: AsyncSession, issue_id: UUID) -> int:
    from ieum.modules.issues.contracts import issue_model

    issue = await session.get(issue_model(), issue_id)
    return int(getattr(issue, "priority", 3) or 3)


async def _created_at_of(session: AsyncSession, issue_id: UUID) -> datetime:
    from ieum.modules.issues.contracts import issue_model

    issue = await session.get(issue_model(), issue_id)
    if issue is None:
        return utcnow()
    return issue.created_at


async def _state_id_of(session: AsyncSession, issue_id: UUID) -> UUID | None:
    ref = await issues.get_issue(session, issue_id)
    return ref.state_id if ref else None
