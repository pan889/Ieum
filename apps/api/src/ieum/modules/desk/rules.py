"""자동화 규칙을 실행한다 (feature-map C9).

`automation.py` 가 조건을 판정하고, 여기가 **행을 쓴다.** 두 층을 나눈 이유는
앞쪽이 순수 함수라 손으로 값을 적어 시험할 수 있기 때문이다.

## 워커에서 돈다

아웃박스 이벤트를 받아 처리한다. 요청 경로에서 돌리면 상담원의 저장 버튼이
규칙 다섯 개를 기다리고, 규칙 하나가 실패하면 저장 자체가 실패한다 — 규칙은
이미 일어난 일에 대한 반응이지 그 일의 일부가 아니다.

## 고리를 막는다

규칙이 코멘트를 남기면 그 코멘트가 다시 `issue.commented` 를 내고, 같은
규칙이 또 걸린다 — 무한 고리다. **자동화가 만든 이벤트에는 표시가 붙고
(`automated`), 자동화는 그 표시가 붙은 이벤트를 아예 안 본다.**

우선순위나 담당자를 바꾸는 조치는 이벤트를 내지 않으므로 고리가 없다. 나중에
그런 조치가 이벤트를 내게 되면 같은 표시를 달아야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.events import EventEnvelope
from ieum.core.logging import get_logger
from ieum.modules.desk import automation
from ieum.modules.desk.models import AutomationRule, CannedResponse, TicketExt
from ieum.modules.issues import contracts as issues

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RuleContext:
    """워커가 이벤트마다 넘기는 것."""

    session: AsyncSession


async def handle_automation(ctx: RuleContext, envelope: EventEnvelope) -> int:
    """이 이벤트로 깨어나는 규칙들을 돌린다. 실행한 규칙 수를 돌려준다.

    아웃박스의 **모든** 이벤트가 여기로 오므로, 관심 없는 것은 곧바로
    돌려보낸다 — 이벤트마다 조회를 하면 드레인이 느려진다.
    """
    if envelope.event_type not in automation.TRIGGERS:
        return 0
    # **자동화가 만든 것은 안 본다.** 이 한 줄이 고리를 막는다.
    if bool(envelope.payload.get("automated")):
        return 0

    session = ctx.session
    issue_id = envelope.aggregate_id
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        # 티켓이 아닌 이슈에는 데스크 자동화를 걸지 않는다. 걸면 이슈 전부가
        # 데스크 규칙의 대상이 되고, 그건 다른 제품이다.
        return 0
    ref = await issues.get_issue(session, issue_id)
    if ref is None:
        return 0

    rules = list(
        (
            await session.execute(
                select(AutomationRule)
                .where(
                    AutomationRule.project_id == ref.project_id,
                    AutomationRule.is_enabled.is_(True),
                    AutomationRule.archived_at.is_(None),
                )
                .order_by(AutomationRule.position, AutomationRule.name)
            )
        )
        .scalars()
        .all()
    )
    if not rules:
        return 0

    facts = await _facts(session, ticket, ref, envelope)
    ran = 0
    for rule in rules:
        if rule.trigger.get("event") != envelope.event_type:
            continue
        if not automation.matches(list(rule.conditions), facts):
            continue
        await _apply(session, rule, issue_id)
        ran += 1
        log.info("desk.automation.ran", rule=str(rule.id), issue_id=str(issue_id))
    await session.flush()
    return ran


# ── 내부 ────────────────────────────────────────────────────────


async def _facts(
    session: AsyncSession,
    ticket: TicketExt,
    ref: issues.IssueRef,
    envelope: EventEnvelope,
) -> automation.TicketFacts:
    """조건이 보는 것을 **한 번에** 모은다.

    조건마다 조회하면 규칙 다섯 개짜리 프로젝트가 이벤트 하나에 수십 번
    왕복한다. 그리고 판정을 순수 함수로 두려면 사실이 먼저 다 모여 있어야
    한다.
    """
    return automation.TicketFacts(
        priority=ref.priority,
        channel=ticket.channel,
        summary=ref.summary,
        request_type_id=ticket.request_type_id,
        organization_id=ticket.organization_id,
        state_category=ref.state_category,
        is_internal=bool(envelope.payload.get("is_internal")),
        to_state_category=str(envelope.payload.get("to_state_category") or ""),
    )


async def _apply(session: AsyncSession, rule: AutomationRule, issue_id: UUID) -> None:
    """규칙의 조치들을 **적은 순서대로** 실행한다.

    한 조치가 실패해도 나머지를 멈추지 않는다 — 담당자를 못 정했다고
    우선순위까지 안 올릴 이유가 없다. 실패는 로그로 남긴다: 자동화의 실패는
    조용해서, 안 남기면 아무 일도 안 일어난 것과 구별되지 않는다.
    """
    for raw in rule.actions:
        action = automation.parse_action(raw)
        try:
            await _one(session, action, issue_id)
        except Exception as exc:
            log.error(
                "desk.automation.action_failed",
                rule=str(rule.id),
                action=action.kind,
                error=f"{type(exc).__name__}: {exc}",
            )


async def _one(session: AsyncSession, action: automation.Action, issue_id: UUID) -> None:
    if action.kind == "set_priority" and action.priority is not None:
        await issues.raise_priority(session, issue_id, to=action.priority)
        return
    if action.kind == "assign" and action.user_id is not None:
        await issues.assign_issue(session, issue_id, to=action.user_id)
        return
    if action.kind in ("reply_with_canned", "add_note") and action.canned_response_id:
        canned = await session.get(CannedResponse, action.canned_response_id)
        if canned is None or canned.archived_at is not None:
            # 지워진 정형 응답. **조용히 넘긴다** — 규칙 하나가 워커를 죽이면
            # 다른 티켓의 자동화도 멈춘다. 로그는 `_apply` 가 남기지 않으므로
            # 여기서 남긴다.
            log.warning("desk.automation.canned_missing", canned=str(action.canned_response_id))
            return
        await issues.add_comment_as_system(
            session,
            issue_id,
            canned.body,
            is_internal=action.kind == "add_note",
        )
