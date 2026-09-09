"""승인 규칙 — 누가, 몇 명이, 무엇을 막는가 (C12).

## 왜 순수 함수인가

승인은 **틀리면 조용히 위험한** 종류다. 두 방향 다 비싸다:

- 통과시켜서는 안 될 것을 통과시키면, 승인을 요구한 요청(권한 부여, 지출)이
  승인 없이 처리된다. 그리고 그 사실은 아무 화면에도 안 나온다.
- 통과시켜야 할 것을 막으면, 요청이 영원히 멈춰 있고 사람은 제품이 고장났다고
  여긴다.

그래서 판정은 DB 없이 값으로 붙잡는다 (`test_desk_approvals.py`).

## 승인자는 **찍어 둔다** (snapshot)

요청하는 순간의 명단을 행에 적는다. 그룹을 그때그때 펼치지 않는 이유:

- 그룹은 바뀐다. 승인을 기다리는 중에 사람이 그룹을 떠나면, 살아 있는 명단은
  "우리가 누구를 기다렸나" 를 잃는다. 이미 승인한 사람이 명단에서 사라지면
  `all` 모드의 셈이 어긋난다.
- 감사에 남아야 하는 것은 "승인할 수 있었던 사람" 이다. 사후에 그룹을 고쳐
  기록을 바꿀 수 있게 두지 않는다.

## 요청자는 자기 요청을 승인하지 못한다

설정으로 두지 않는다. 자기 승인이 허용되는 순간 이 기능은 서류 작업이 된다.
그리고 **명단을 찍을 때 빼 둔다** — 결정할 때 거절하면 "승인자가 셋인데 아무도
승인할 수 없는" 상태가 화면에 안 보이기 때문이다. 빼 두면 곧바로 "승인자가
없다" 로 보이고, 상담원이 그 자리에서 취소할 수 있다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.outbox import publish
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as perms
from ieum.modules.desk.events import ApprovalDecided, ApprovalRequested
from ieum.modules.desk.models import (
    APPROVAL_DECISIONS,
    APPROVAL_MODES,
    Approval,
    ApprovalVote,
    RequestType,
)
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues
from ieum.modules.issues import gates

log = get_logger(__name__)

#: 한 요청 유형에 적을 수 있는 승인자·그룹 수. 화면이 명단을 그려야 한다.
MAX_APPROVERS = 20
MAX_GROUPS = 10


@dataclass(frozen=True, slots=True)
class Rule:
    """요청 유형에 붙는 승인 규칙. `request_type.approval` 의 읽은 모양이다."""

    mode: str
    user_ids: tuple[UUID, ...]
    group_ids: tuple[UUID, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "user_ids": [str(value) for value in self.user_ids],
            "group_ids": [str(value) for value in self.group_ids],
        }


def parse_rule(raw: Any) -> Rule | None:
    """저장된 JSONB 를 읽는다. 없으면 `None` — 승인이 필요 없는 유형이다.

    읽을 수 없는 값이면 **거절하지 않고 `None`** 을 준다. 여기서 예외를 내면
    설정이 망가진 요청 유형 하나가 포털 전체를 500 으로 만든다. 저장 시점에
    `validate_rule` 이 막으므로 이 자리에 올 일은 DB 를 직접 고친 경우뿐이다.
    """
    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    if mode not in APPROVAL_MODES:
        return None
    return Rule(
        mode=str(mode),
        user_ids=tuple(_uuids(raw.get("user_ids"))),
        group_ids=tuple(_uuids(raw.get("group_ids"))),
    )


def validate_rule(raw: Any) -> Rule:
    """저장 전 검증. **여기서 막아야** 켜 놓고 아무 일도 안 일어나지 않는다."""
    if not isinstance(raw, dict):
        raise ValidationError("승인 규칙을 읽을 수 없다.", code="desk.invalid_approval")
    mode = raw.get("mode")
    if mode not in APPROVAL_MODES:
        raise ValidationError(
            "승인 방식을 고르지 않았다.",
            code="desk.invalid_approval_mode",
            details={"allowed": ", ".join(APPROVAL_MODES)},
        )
    users = list(dict.fromkeys(_uuids(raw.get("user_ids"))))
    groups = list(dict.fromkeys(_uuids(raw.get("group_ids"))))
    if not users and not groups:
        # 승인자 없는 규칙은 **요청을 영원히 멈추는 스위치**다.
        raise ValidationError("승인자를 한 명 이상 정해야 한다.", code="desk.approvers_required")
    if len(users) > MAX_APPROVERS:
        raise ValidationError(f"승인자는 {MAX_APPROVERS}명까지다.", code="desk.too_many_approvers")
    if len(groups) > MAX_GROUPS:
        raise ValidationError(f"그룹은 {MAX_GROUPS}개까지다.", code="desk.too_many_approvers")
    return Rule(mode=str(mode), user_ids=tuple(users), group_ids=tuple(groups))


def snapshot(
    *,
    rule: Rule,
    group_members: Iterable[UUID],
    active_user_ids: Iterable[UUID],
    reporter_id: UUID | None,
) -> list[UUID]:
    """이 승인을 줄 수 있는 사람들. 순서는 안정적이다(찍은 값이라 정렬한다).

    `active_user_ids` 로 한 번 더 거르는 이유: 정지된 계정이 명단에 있으면
    `all` 모드가 절대 완성되지 않는다. 그건 "승인을 기다린다" 가 아니라
    멈춘 것이고, 화면은 둘을 구별해 주지 못한다.

    요청자는 뺀다 (파일 머리 참조).
    """
    live = set(active_user_ids)
    found = {uid for uid in (*rule.user_ids, *group_members) if uid in live}
    if reporter_id is not None:
        found.discard(reporter_id)
    return sorted(found)


@dataclass(frozen=True, slots=True)
class Vote:
    user_id: UUID
    decision: str


def outcome(*, mode: str, approver_ids: Sequence[UUID], votes: Sequence[Vote]) -> str:
    """이 표들로 결론이 났는가. `pending`·`approved`·`declined` 중 하나.

    **거절은 즉시 끝난다** — `all` 모드에서 한 명이 거절하면 나머지에게 물어도
    답이 달라지지 않고, 물어보는 동안 요청이 멈춰 있다.
    """
    if any(vote.decision == "decline" for vote in votes):
        return "declined"
    approved = {vote.user_id for vote in votes if vote.decision == "approve"}
    if not approved:
        return "pending"
    if mode == "one":
        return "approved"
    # `all`. 명단이 비어 있으면 "전원 승인" 이 참이 되어 버린다 — 그건 승인이
    # 아니라 통과다. 명단이 빈 승인은 사람이 취소해야 한다.
    if not approver_ids:
        return "pending"
    return "approved" if approved >= set(approver_ids) else "pending"


def blocks(*, to_category: str) -> bool:
    """승인을 기다리는 티켓이 이 갈래로 갈 수 있나.

    **`todo` 안에서만 움직인다.** 착수(`in_progress`)와 종료(`done`)를 둘 다
    막는 이유: 하나만 막으면 남은 길로 돌아간다. 착수만 막으면 상담원이
    바로 "해결" 로 보낼 수 있고, 그러면 승인은 없던 것이 된다.

    `todo` 를 열어 두는 것은 분류를 위해서다 — 승인을 기다리는 동안 우선순위
    상태를 바꾸거나 접수 상태를 정리하는 것은 요청을 처리하는 일이 아니다.

    그리고 이 문을 여는 손잡이는 **승인 취소**다(`STATUSES` 주석 참조).
    """
    return to_category in ("in_progress", "done")


def _uuids(raw: Any) -> list[UUID]:
    """읽을 수 있는 것만 UUID 로. 못 읽는 값은 버린다 — 설정 하나가
    포털을 500 으로 만들지 않게 한다."""
    if not isinstance(raw, list):
        return []
    out: list[UUID] = []
    for value in raw:
        try:
            out.append(UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


__all__ = [
    "MAX_APPROVERS",
    "MAX_GROUPS",
    "PENDING_CODE",
    "ApprovalService",
    "ApprovalView",
    "Approver",
    "Rule",
    "Vote",
    "VoteView",
    "blocks",
    "gate",
    "install",
    "outcome",
    "parse_rule",
    "request_for",
    "snapshot",
    "validate_rule",
]


# ── 서비스 ──────────────────────────────────────────────────────
#
# 위쪽 판정은 값만 다룬다. 아래쪽이 그것을 DB 와 잇는다.


@dataclass(frozen=True, slots=True)
class VoteView:
    """표 하나 + 누가 냈나."""

    user_id: UUID
    display_name: str
    decision: str
    comment: str | None
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """화면이 그릴 승인 하나."""

    id: UUID
    issue_id: UUID
    issue_key: str
    summary: str
    mode: str
    status: str
    requested_at: datetime
    decided_at: datetime | None
    #: 찍어 둔 명단. 이름까지 담는다 — 화면이 사람 조회를 또 하지 않게.
    approvers: tuple[Approver, ...]
    votes: tuple[VoteView, ...]
    #: 지금 보는 사람이 결정할 수 있나. 명단에 있고 아직 안 냈으면 참이다.
    can_decide: bool


@dataclass(frozen=True, slots=True)
class Approver:
    user_id: UUID
    display_name: str


async def request_for(
    session: AsyncSession,
    *,
    issue_id: UUID,
    request_type: RequestType,
    reporter_id: UUID | None,
) -> Approval | None:
    """이 요청 유형이 승인을 요구하면 승인을 하나 만든다.

    **티켓을 만드는 트랜잭션 안에서 부른다.** 이벤트로 뒤에 처리하면 아웃박스가
    도는 사이(기본 15초)에 승인 없는 티켓이 열려 있고, 그 틈에 상담원이 착수할
    수 있다. 문을 여는 기능에서 그 틈은 실제 구멍이다.

    **권한을 보지 않는다.** 고객의 제출 경로에서 불리고, 승인을 요구하는 것은
    사람의 결정이 아니라 요청 유형의 설정이다. 액터를 받으면 언젠가 누군가
    `require` 를 부르고, 그날 고객의 제출이 403 으로 막힌다.

    승인자가 하나도 안 남으면 **그래도 만든다.** 안 만들면 승인이 필요한
    요청이 승인 없이 처리되고 아무 화면에도 그 사실이 안 나온다 — 만들면
    "승인자가 없다" 로 보이고 상담원이 취소할 수 있다(`approvals.py` 머리).
    """
    rule = parse_rule(request_type.approval)
    if rule is None:
        return None

    members = await identity.active_group_members(session, rule.group_ids)
    # 명단에 오를 수 있는 사람. **고객 계정은 뺀다** — 고객은 포털 밖을 볼 수
    # 없어서 승인 화면에 닿지 못한다(설정 화면도 막지만, 그 뒤에 계정이
    # 고객으로 바뀌거나 그룹에 고객이 들어올 수 있다).
    people = await identity.get_users(session, {*rule.user_ids, *members})
    eligible = {uid for uid, ref in people.items() if ref.is_active and not ref.is_customer}
    approver_ids = snapshot(
        rule=rule,
        group_members=members,
        active_user_ids=eligible,
        reporter_id=reporter_id,
    )
    row = Approval(
        issue_id=issue_id,
        request_type_id=request_type.id,
        mode=rule.mode,
        status="pending",
        approver_ids=[str(value) for value in approver_ids],
    )
    session.add(row)
    await session.flush()
    log.info(
        "desk.approval_requested",
        issue=str(issue_id),
        approval=str(row.id),
        approvers=len(approver_ids),
    )
    ticket = await _ticket_of(session, issue_id)
    publish(
        session,
        ApprovalRequested(
            aggregate_id=issue_id,
            project_id=await _project_of(session, issue_id),
            issue_key=ticket[0],
            summary=ticket[1],
            approval_id=row.id,
            to_user_ids=list(approver_ids),
        ),
    )
    return row


class ApprovalService:
    """승인을 읽고, 결정하고, 취소한다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def for_issue(self, actor: Actor, issue_id: UUID) -> list[ApprovalView]:
        """이 티켓의 승인 이력. 최신순.

        **이슈를 볼 수 있어야 본다.** 승인 이력에는 거절 이유가 들어 있고,
        그건 티켓 내용만큼 민감하다.
        """
        ref = await issues.get_issue(self._s, issue_id)
        if ref is None:
            raise NotFoundError("티켓을 찾을 수 없다.")
        await self._perms.require(
            self._s,
            actor,
            "issue.view",
            scope=Scope.project(ref.project_id),
            subject=await self._s.get(issues.issue_model(), issue_id),
        )
        rows = list(
            (
                await self._s.execute(
                    select(Approval)
                    .where(Approval.issue_id == issue_id)
                    .order_by(Approval.requested_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return await self._views(actor, rows)

    async def mine(self, actor: Actor, *, limit: int = 20) -> list[ApprovalView]:
        """내가 결정해야 할 것. **아직 안 낸 것만.**

        권한을 보지 않는다. 근거는 권한이 아니라 **명단**이다 — 승인자로
        찍힌 사람은 그 티켓의 프로젝트 권한이 없을 수 있고(부서장, 고객),
        그래도 승인은 해야 한다. 그래서 여기서 내주는 것은 승인에 필요한
        만큼으로 좁힌다: 키·제목·명단·표.
        """
        rows = list(
            (
                await self._s.execute(
                    select(Approval)
                    .where(
                        Approval.status == "pending",
                        Approval.approver_ids.contains([str(actor.user_id)]),
                    )
                    .order_by(Approval.requested_at.asc())
                    .limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
        views = await self._views(actor, rows)
        return [view for view in views if view.can_decide]

    async def decide(
        self, actor: Actor, approval_id: UUID, *, decision: str, comment: str | None = None
    ) -> ApprovalView:
        """승인하거나 거절한다. **명단에 있는 사람만.**"""
        if decision not in APPROVAL_DECISIONS:
            raise ValidationError(
                "그런 결정은 없다.",
                code="desk.invalid_decision",
                details={"allowed": ", ".join(APPROVAL_DECISIONS)},
            )
        row = await self._require(approval_id)
        if row.status != "pending":
            raise ConflictError("이미 끝난 승인이다.", code="desk.approval_closed")
        if str(actor.user_id) not in row.approver_ids:
            # 403 이다. 승인이 **있다는 사실**은 티켓을 볼 수 있으면 이미
            # 보이므로 감출 것이 없고, "당신은 승인자가 아니다" 가 사실이다.
            raise PermissionDeniedError("이 승인의 승인자가 아니다.", code="desk.not_an_approver")

        self._s.add(
            ApprovalVote(
                approval_id=row.id,
                user_id=actor.user_id,
                decision=decision,
                comment=(comment or "").strip()[:2000] or None,
            )
        )
        try:
            await self._s.flush()
        except IntegrityError as exc:
            # 유니크가 막았다. 두 탭에서 두 번 누른 경우다.
            raise ConflictError("이미 결정했다.", code="desk.already_decided") from exc

        votes = await self._votes(row.id)
        result = outcome(
            mode=row.mode,
            approver_ids=[UUID(value) for value in row.approver_ids],
            votes=[Vote(user_id=v.user_id, decision=v.decision) for v in votes],
        )
        if result != "pending":
            row.status = result
            row.decided_at = utcnow()
            ticket = await _ticket_of(self._s, row.issue_id)
            publish(
                self._s,
                ApprovalDecided(
                    aggregate_id=row.issue_id,
                    project_id=await _project_of(self._s, row.issue_id),
                    issue_key=ticket[0],
                    summary=ticket[1],
                    approval_id=row.id,
                    status=result,
                    actor_id=actor.user_id,
                ),
            )
        await self._s.flush()
        log.info(
            "desk.approval_decided",
            approval=str(row.id),
            actor=str(actor.user_id),
            decision=decision,
            status=row.status,
        )
        (view,) = await self._views(actor, [row])
        return view

    async def cancel(self, actor: Actor, approval_id: UUID) -> ApprovalView:
        """기다리는 승인을 접는다. **문을 여는 손잡이다.**

        명단이 비거나 요청자가 물러선 티켓은 이것 없이는 영원히 멈춘다.
        누가 열었는지 행에 남는다.
        """
        row = await self._require(approval_id)
        ref = await issues.get_issue(self._s, row.issue_id)
        if ref is None:
            raise NotFoundError("티켓을 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.APPROVAL_MANAGE, scope=Scope.project(ref.project_id)
        )
        if row.status != "pending":
            raise ConflictError("이미 끝난 승인이다.", code="desk.approval_closed")
        row.status = "cancelled"
        row.decided_at = utcnow()
        row.cancelled_by = actor.user_id
        await self._s.flush()
        log.info("desk.approval_cancelled", approval=str(row.id), actor=str(actor.user_id))
        (view,) = await self._views(actor, [row])
        return view

    # ── 내부 ────────────────────────────────────────────────────

    async def _require(self, approval_id: UUID) -> Approval:
        row = await self._s.get(Approval, approval_id)
        if row is None:
            raise NotFoundError("승인을 찾을 수 없다.")
        return row

    async def _votes(self, approval_id: UUID) -> list[ApprovalVote]:
        return list(
            (
                await self._s.execute(
                    select(ApprovalVote)
                    .where(ApprovalVote.approval_id == approval_id)
                    .order_by(ApprovalVote.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def _views(self, actor: Actor, rows: list[Approval]) -> list[ApprovalView]:
        if not rows:
            return []
        # 이름은 한 번에 읽는다. 행마다 조회하면 목록이 N+1 이 된다.
        wanted: set[UUID] = set()
        votes: dict[UUID, list[ApprovalVote]] = {}
        for row in rows:
            wanted |= {UUID(value) for value in row.approver_ids}
            votes[row.id] = await self._votes(row.id)
            wanted |= {vote.user_id for vote in votes[row.id]}
        people = await identity.get_users(self._s, wanted)
        tickets = await issues.get_tickets(self._s, [row.issue_id for row in rows])

        def name(user_id: UUID) -> str:
            found = people.get(user_id)
            return found.display_name if found else ""

        out: list[ApprovalView] = []
        for row in rows:
            ticket = tickets.get(row.issue_id)
            mine = str(actor.user_id) in row.approver_ids
            already = any(vote.user_id == actor.user_id for vote in votes[row.id])
            out.append(
                ApprovalView(
                    id=row.id,
                    issue_id=row.issue_id,
                    issue_key=ticket.key if ticket else "",
                    summary=ticket.summary if ticket else "",
                    mode=row.mode,
                    status=row.status,
                    requested_at=row.requested_at,
                    decided_at=row.decided_at,
                    approvers=tuple(
                        Approver(user_id=UUID(value), display_name=name(UUID(value)))
                        for value in row.approver_ids
                    ),
                    votes=tuple(
                        VoteView(
                            user_id=vote.user_id,
                            display_name=name(vote.user_id),
                            decision=vote.decision,
                            comment=vote.comment,
                            decided_at=vote.created_at,
                        )
                        for vote in votes[row.id]
                    ),
                    can_decide=row.status == "pending" and mine and not already,
                )
            )
        return out


# ── 전이 관문 ───────────────────────────────────────────────────


#: 승인을 기다려서 막았다는 코드. **상수로 둔다** — 에러 코드 게이트가
#: `code=` 로 넘기는 상수를 풀어 보므로, 이렇게 두면 번역 없이 배포되는 일이
#: 검사에서 걸린다 (`test_i18n_error_codes.py`).
PENDING_CODE = "desk.approval_pending"


async def gate(session: AsyncSession, actor: Actor, request: gates.Gate) -> None:
    """승인을 기다리는 티켓은 착수·종료로 못 간다 (`blocks` 참조).

    **액터를 보지 않는다.** 관리자라고 통과시키면 승인은 요청하는 사람의
    성실함에 달린 것이 되고, 그건 감사 대상이 되지 못한다. 문을 여는 길은
    승인·거절·취소뿐이다.
    """
    del actor
    if not blocks(to_category=request.to_category):
        return
    found = await session.scalar(
        select(Approval.id).where(
            Approval.issue_id == request.issue_id, Approval.status == "pending"
        )
    )
    if found is not None:
        raise ConflictError("승인을 기다리는 요청이다.", code=PENDING_CODE)


def install() -> None:
    """기동 시 한 번 부른다 (`wiring.py`)."""
    gates.register(gate)


async def _project_of(session: AsyncSession, issue_id: UUID) -> UUID:
    ref = await issues.get_issue(session, issue_id)
    if ref is None:  # pragma: no cover - 같은 트랜잭션 안이라 있다
        raise NotFoundError("티켓을 찾을 수 없다.")
    return ref.project_id


async def _ticket_of(session: AsyncSession, issue_id: UUID) -> tuple[str, str]:
    """알림 제목이 쓸 `(키, 제목)`.

    **둘을 함께 읽는다.** 따로 두면 조회가 두 번이고, 무엇보다 제목을 안 실어
    보내면 알림이 "ENG-12 의 승인이 필요합니다: " 로 나간다 — 무엇을 승인하는지
    없는 알림이다.
    """
    found = await issues.get_tickets(session, [issue_id])
    ticket = found.get(issue_id)
    return (ticket.key, ticket.summary) if ticket else ("", "")
