"""상담원의 공개 회신을 메일로 (feature-map C6).

`inbound.py` 의 짝이다. 메일로 온 티켓은 **메일로 답해야** 대화가 이어진다 —
고객은 포털 주소를 모르고, 알아도 메일에 답하는 것이 그 사람의 습관이다.

## 왜 워커에서 도는가

`issue.commented` 를 아웃박스에서 받아 처리한다. 요청 경로에서 보내면 SMTP 가
느린 날 상담원의 저장 버튼이 몇 초씩 멈추고, 메일 서버가 죽으면 회신 자체가
실패한다 — 회신은 이미 저장된 사실이고 발송은 그 뒤의 일이다.

## 보내지 않는 경우

- **내부 노트.** 이 조건이 이 파일의 자물쇠다: 내부 노트가 고객에게 나가면
  되돌릴 방법이 없다.
- **고객 자신의 회신.** 자기가 보낸 메일이 되돌아오면 무한 고리가 된다.
- **반송된 주소.** 검증되지 않은 게스트 주소로 계속 보내면, 남의 주소를 적어
  넣은 경우 그 사람에게 계속 배달을 시도한다.
- **메일로 오지 않은 티켓.** 포털로 온 요청에 메일을 보내면 고객은 왜 이
  주소로 오는지 모른다 — 포털 알림은 `notify` 가 이미 보낸다.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import make_msgid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.desk.email import thread_subject
from ieum.modules.desk.models import EmailChannel, EmailMessage, TicketExt
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues
from ieum.modules.notify.contracts import Mail

log = get_logger(__name__)


@dataclass(slots=True)
class OutboundContext:
    """워커가 이벤트마다 넘기는 것."""

    session: AsyncSession
    settings: Settings


async def collect_reply_mail(ctx: OutboundContext, envelope: EventEnvelope) -> list[Mail]:
    """이 이벤트가 메일로 나갈 회신이면 그 한 통을. 아니면 빈 목록.

    `identity.collect_invite_mail` 과 같은 모양이다 — 워커가 트랜잭션 안에서
    모으고 **닫은 뒤에** 보낸다.

    보낼 것이 있으면 `email_message` 행도 여기서 남긴다. 그 행이 없으면 고객의
    회신이 어디에 붙는지 알 수 없다 — 스레드가 우리 쪽에서 끊긴다.
    """
    if envelope.event_type != "issue.commented":
        return []
    # **내부 노트는 안 나간다.** 이 한 줄이 이 파일의 자물쇠다.
    if bool(envelope.payload.get("is_internal")):
        return []

    session = ctx.session
    issue_id = envelope.aggregate_id
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None or ticket.channel != "email":
        return []
    if ticket.email_bounced_at is not None:
        log.info("desk.email.suppressed", issue_id=str(issue_id), reason="bounced")
        return []

    to = await _requester_address(session, ticket)
    if not to:
        return []
    # 고객 자신의 회신이면 되돌려 보내지 않는다. 무한 고리가 된다.
    actor_id = envelope.uuid("actor_id")
    if actor_id is not None and actor_id == ticket.reporter_customer_id:
        return []
    # **본문은 페이로드가 아니라 계약으로 읽는다.** `get_public_comment` 는
    # 내부 노트에 `None` 을 돌려주므로, 위의 `is_internal` 검사가 틀렸더라도
    # 여기서 막힌다 — 자물쇠가 둘이다.
    comment_id = envelope.uuid("comment_id")
    if comment_id is None:
        return []
    comment = await issues.get_public_comment(session, comment_id)
    if comment is None:
        return []
    body = comment.body.strip()
    if not body:
        return []

    channel = await _channel_of(session, ticket)
    if channel is None:
        return []

    ref = await issues.get_issue(session, issue_id)
    if ref is None:
        return []
    key = str(envelope.payload.get("issue_key") or "")
    summary = ref.summary

    parent, references = await _thread_of(session, issue_id)
    # **우리가 message-id 를 정한다.** 발송 라이브러리에 맡기면 우리가 보낸
    # id 를 모르고, 고객의 회신에 그 id 가 실려 와도 이을 수 없다.
    message_id = make_msgid(domain=channel.outbound_from.rpartition("@")[2] or None)
    headers: list[tuple[str, str]] = [("Message-ID", message_id)]
    if parent:
        headers.append(("In-Reply-To", parent))
        chain = [*references, parent] if parent not in references else list(references)
        headers.append(("References", " ".join(chain)))

    session.add(
        EmailMessage(
            issue_id=issue_id,
            channel_id=channel.id,
            message_id=message_id,
            in_reply_to=parent,
            direction="outbound",
            from_email=channel.outbound_from,
            subject=thread_subject(key, summary)[:998],
            processed_at=utcnow(),
        )
    )
    await session.flush()
    return [
        Mail(
            to=to,
            subject=thread_subject(key, summary),
            body=body,
            from_address=channel.outbound_from,
            headers=tuple(headers),
        )
    ]


# ── 내부 ────────────────────────────────────────────────────────


async def _requester_address(session: AsyncSession, ticket: TicketExt) -> str:
    if ticket.guest_email:
        return ticket.guest_email
    if ticket.reporter_customer_id is None:
        return ""
    ref = await identity.get_user(session, ticket.reporter_customer_id)
    return ref.email if ref else ""


async def _channel_of(session: AsyncSession, ticket: TicketExt) -> EmailChannel | None:
    """이 티켓이 들어온 채널.

    `email_message` 를 되짚는다. `ticket_ext` 에 채널 id 를 두지 않은 이유:
    채널은 **주소**이고 티켓은 대화다 — 주소가 바뀌거나 채널이 지워져도 지난
    대화의 스레드는 그대로여야 한다. 되짚을 것이 없으면(기록이 지워졌으면)
    프로젝트의 채널 하나를 쓴다.
    """
    row = (
        await session.execute(
            select(EmailMessage.channel_id)
            .where(EmailMessage.issue_id == ticket.issue_id, EmailMessage.channel_id.isnot(None))
            .order_by(EmailMessage.created_at)
            .limit(1)
        )
    ).first()
    if row is not None and row[0] is not None:
        found = await session.get(EmailChannel, row[0])
        if found is not None and found.archived_at is None:
            return found
    ref = await issues.get_issue(session, ticket.issue_id)
    if ref is None:
        return None
    return (
        await session.execute(
            select(EmailChannel)
            .where(
                EmailChannel.project_id == ref.project_id,
                EmailChannel.archived_at.is_(None),
            )
            .order_by(EmailChannel.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _thread_of(session: AsyncSession, issue_id: UUID) -> tuple[str | None, list[str]]:
    """이 티켓의 마지막 메일과 그 앞의 사슬.

    **마지막 받은 메일에 답한다.** 우리가 보낸 것에 답하면 고객의 메일함에서
    우리 회신이 우리 회신에 이어지고, 고객이 마지막으로 쓴 글과 이어지지
    않는다.
    """
    rows = list(
        (
            await session.execute(
                select(EmailMessage.message_id, EmailMessage.direction)
                .where(EmailMessage.issue_id == issue_id)
                .order_by(EmailMessage.created_at)
            )
        ).all()
    )
    if not rows:
        return None, []
    chain = [str(message_id) for message_id, _direction in rows]
    inbound_ids = [str(message_id) for message_id, direction in rows if direction == "inbound"]
    parent = inbound_ids[-1] if inbound_ids else chain[-1]
    return parent, [value for value in chain if value != parent]
