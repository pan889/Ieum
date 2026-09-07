"""받은 메일을 티켓으로 (feature-map C6).

`email.py` 가 원문을 읽고, 여기가 **행을 쓴다** — 어느 티켓에 붙일지 정하고,
없으면 만든다. 층을 나눈 이유는 앞쪽이 순수 함수라 원문 바이트를 손으로 적어
시험할 수 있기 때문이다.

## 이 파일의 위험한 부분

**주소가 맞을 때만 붙인다.** 스레드는 `In-Reply-To` 로 찾지만 그 헤더는
누구나 적을 수 있다. 헤더만 믿으면, message-id 를 아는 사람이 남의 티켓에
글을 남기고 **그 티켓의 주인이 그것을 읽는다.** 그래서 찾은 티켓의 요청자
주소와 보낸 주소가 다르면 붙이지 않고 새 티켓을 만든다 — 잘못 갈라진 티켓은
상담원이 합칠 수 있지만, 남의 대화에 섞여 들어간 글은 되돌릴 수 없다.

**자동 응답은 붙이지 않는다.** 붙이면 우리가 보낸 접수 알림에 고객의 휴가
알림이 답하고, 그 답에 우리가 또 알림을 보내는 고리가 생긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.logging import get_logger
from ieum.core.outbox import publish
from ieum.core.permissions import PermissionService
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.modules.desk.email import ParsedEmail, ticket_key_in
from ieum.modules.desk.events import TicketSubmitted
from ieum.modules.desk.models import EmailChannel, EmailMessage, RequestType, TicketExt
from ieum.modules.desk.repository import CustomerOrganizationRepository
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues

log = get_logger(__name__)

#: 왜 티켓에 반영하지 않았는가. 화면과 로그가 이 값을 그대로 쓴다.
SKIP_DUPLICATE = "duplicate"
SKIP_BOUNCE = "bounce"
SKIP_AUTO_REPLY = "auto_reply"
SKIP_NO_BODY = "empty"

#: 제목이 없는 메일의 요약. 비워 두면 목록에서 빈 줄이 된다.
NO_SUBJECT = "(제목 없음)"

#: 요약의 상한. 이슈의 `summary` 가 그만큼 받는다.
SUMMARY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class InboundResult:
    """메일 한 통을 처리한 결과."""

    issue_id: UUID | None
    #: 새 티켓을 만들었는가. 아니면 기존 티켓에 붙였다.
    created: bool = False
    #: 반영하지 않았으면 그 이유. 반영했으면 `None`.
    skipped_reason: str | None = None


async def handle_inbound(
    session: AsyncSession,
    permissions: PermissionService,
    *,
    channel: EmailChannel,
    parsed: ParsedEmail,
    raw: bytes,
    store: ObjectStore | None = None,
) -> InboundResult:
    """메일 한 통을 티켓에 반영한다. 기록은 **어떤 경우에도** 남긴다.

    반영하지 않은 메일도 행을 남기는 이유: "왜 이 메일이 안 들어왔나" 의 답이
    그것뿐이다. 아무 것도 안 남기면 관리자는 메일 서버를 뒤진다.
    """
    if await _already_seen(session, parsed.message_id):
        # IMAP 은 같은 메일을 두 번 줄 수 있다(폴링 중 연결이 끊기면).
        # 그때 티켓에 같은 코멘트가 둘 생긴다.
        return InboundResult(issue_id=None, skipped_reason=SKIP_DUPLICATE)

    raw_key = await _store_raw(store, channel, parsed, raw)

    if parsed.is_bounce:
        issue_id = await _mark_bounced(session, channel, parsed)
        _record(session, channel, parsed, issue_id=issue_id, raw_key=raw_key, skipped=SKIP_BOUNCE)
        await session.flush()
        return InboundResult(issue_id=issue_id, skipped_reason=SKIP_BOUNCE)

    if parsed.is_auto_reply:
        _record(session, channel, parsed, issue_id=None, raw_key=raw_key, skipped=SKIP_AUTO_REPLY)
        await session.flush()
        return InboundResult(issue_id=None, skipped_reason=SKIP_AUTO_REPLY)

    body = parsed.body.strip()
    if not body and not parsed.attachments:
        # 본문도 첨부도 없다. 티켓을 만들면 상담원이 열어 보고 아무 것도
        # 못 찾는다.
        _record(session, channel, parsed, issue_id=None, raw_key=raw_key, skipped=SKIP_NO_BODY)
        await session.flush()
        return InboundResult(issue_id=None, skipped_reason=SKIP_NO_BODY)

    matched = await match_thread(session, channel=channel, parsed=parsed)
    if matched is not None:
        await issues.add_public_comment(
            session,
            _sender_actor(parsed.from_email),
            matched,
            body or NO_SUBJECT,
            anonymous=True,
        )
        _record(session, channel, parsed, issue_id=matched, raw_key=raw_key, skipped=None)
        await session.flush()
        log.info("desk.email.appended", issue_id=str(matched), channel=str(channel.id))
        return InboundResult(issue_id=matched, created=False)

    issue_id = await _create_ticket(session, permissions, channel=channel, parsed=parsed)
    _record(session, channel, parsed, issue_id=issue_id, raw_key=raw_key, skipped=None)
    await session.flush()
    log.info("desk.email.created", issue_id=str(issue_id), channel=str(channel.id))
    return InboundResult(issue_id=issue_id, created=True)


async def match_thread(
    session: AsyncSession, *, channel: EmailChannel, parsed: ParsedEmail
) -> UUID | None:
    """이 메일이 이어지는 티켓. 없거나 **주인이 아니면** `None`.

    두 근거를 순서대로 본다:

    1. `In-Reply-To` 와 `References` — 우리가 보낸 메일의 message-id 다.
    2. 제목의 티켓 키 — 헤더를 지우는 클라이언트가 있고, 사람이 옛 메일을
       복사해 새로 쓰기도 한다.

    그리고 **둘 다 주소를 확인한다.** 근거가 헤더든 제목이든 위조할 수 있고,
    남의 티켓에 붙은 글은 그 티켓의 주인이 읽는다.
    """
    candidate = await _by_headers(session, parsed)
    if candidate is None:
        candidate = await _by_subject(session, channel=channel, parsed=parsed)
    if candidate is None:
        return None
    return candidate if await _sender_owns(session, candidate, parsed.from_email) else None


# ── 내부 ────────────────────────────────────────────────────────


async def _already_seen(session: AsyncSession, message_id: str) -> bool:
    found = await session.execute(
        select(EmailMessage.id).where(EmailMessage.message_id == message_id).limit(1)
    )
    return found.first() is not None


async def _by_headers(session: AsyncSession, parsed: ParsedEmail) -> UUID | None:
    """우리가 보낸 메일의 id 로 찾는다. **가장 최근 것부터** 본다.

    `References` 는 오래된 것부터 오므로 뒤집어 본다 — 긴 스레드가 갈라졌을
    때 사람이 뜻한 것은 방금 답한 메일이다.
    """
    wanted = [parsed.in_reply_to, *reversed(parsed.references)]
    for message_id in [value for value in wanted if value]:
        row = (
            await session.execute(
                select(EmailMessage.issue_id).where(EmailMessage.message_id == message_id).limit(1)
            )
        ).first()
        if row is not None and row[0] is not None:
            return UUID(str(row[0]))
    return None


async def _by_subject(
    session: AsyncSession, *, channel: EmailChannel, parsed: ParsedEmail
) -> UUID | None:
    """제목의 티켓 키로 찾는다. **이 채널의 프로젝트 안에서만.**

    프로젝트를 안 보면 다른 프로젝트의 키를 적은 메일이 그 티켓에 붙는다 —
    채널을 프로젝트마다 두는 의미가 없어진다.
    """
    key = ticket_key_in(parsed.subject)
    if key is None:
        return None
    ref = await issues.get_issue_by_key(session, key)
    if ref is None or ref.project_id != channel.project_id:
        return None
    # 티켓이 아닌 이슈에는 붙이지 않는다. 메일로 아무 이슈에 코멘트를 달 수
    # 있게 되면 그건 데스크가 아니라 우회로다.
    if await session.get(TicketExt, ref.id) is None:
        return None
    return ref.id


async def _sender_owns(session: AsyncSession, issue_id: UUID, from_email: str) -> bool:
    """보낸 사람이 이 티켓의 요청자인가.

    **이것이 이 파일의 자물쇠다.** 헤더는 누구나 적을 수 있으므로, 근거가
    맞아도 주소가 다르면 붙이지 않는다.

    담당 상담원이 메일로 답하는 경우는 여기서 통과하지 않는다 — 상담원의 회신
    통로는 상담원 화면이고, 메일로 온 것을 상담원 회신으로 받아들이면 "고객에게
    보이는 글" 을 주소 확인만으로 만들 수 있게 된다.
    """
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        return False
    if ticket.guest_email and ticket.guest_email.lower() == from_email:
        return True
    if ticket.reporter_customer_id is None:
        return False
    ref = await identity.get_user(session, ticket.reporter_customer_id)
    return ref is not None and ref.email.lower() == from_email


async def _create_ticket(
    session: AsyncSession,
    permissions: PermissionService,
    *,
    channel: EmailChannel,
    parsed: ParsedEmail,
) -> UUID:
    """새 티켓. 채널이 정한 요청 유형으로 만든다.

    **계정이 있는 주소라도 그 계정의 티켓으로 만들지 않는다.** 메일의 From 은
    검증되지 않은 값이고(SPF 가 있어도 우리가 판정하지 않는다), 증명 없이 남의
    이름으로 티켓을 만드는 일이 된다 — 게스트 제출과 같은 판단이다.
    """
    request_type = await session.get(RequestType, channel.default_request_type_id)
    if request_type is None:
        raise ValueError("채널의 요청 유형이 없다")

    summary = (parsed.subject.strip() or NO_SUBJECT)[:SUMMARY_LIMIT]
    organization = await CustomerOrganizationRepository(session).find_by_domain(
        parsed.from_email.rpartition("@")[2]
    )
    issue = await issues.create_ticket(
        session,
        permissions,
        _sender_actor(parsed.from_email),
        project_id=channel.project_id,
        issue_type_id=request_type.issue_type_id,
        summary=summary,
        description=parsed.body.strip() or None,
        custom_fields={},
        anonymous=True,
    )
    session.add(
        TicketExt(
            issue_id=issue.id,
            request_type_id=request_type.id,
            reporter_customer_id=None,
            guest_email=parsed.from_email,
            guest_name=parsed.from_name.strip(),
            channel="email",
            organization_id=organization.id if organization else None,
        )
    )
    await session.flush()
    publish(
        session,
        TicketSubmitted(
            aggregate_id=issue.id,
            project_id=channel.project_id,
            issue_key=issue.key,
            request_type_id=request_type.id,
            guest_email=parsed.from_email,
            organization_id=organization.id if organization else None,
        ),
    )
    return issue.id


async def _mark_bounced(
    session: AsyncSession, channel: EmailChannel, parsed: ParsedEmail
) -> UUID | None:
    """반송이 말하는 주소로 그만 보내게 한다.

    어느 티켓인지는 스레드로 찾는다(반송 메일은 원본을 인용해 오므로
    `In-Reply-To` 가 있다). 못 찾으면 주소만으로 찾는다 — 그 주소의 안 끝난
    티켓 전부를 표시한다.

    **주소를 짐작하지 않는다.** `bounced_address` 가 비어 있으면 아무 것도
    하지 않는다: 틀린 주소를 표시하면 멀쩡한 고객에게 메일이 끊긴다.
    """
    address = parsed.bounced_address
    if not address:
        return None
    rows = list(
        (
            await session.execute(
                select(TicketExt).where(
                    TicketExt.guest_email == address,
                    TicketExt.email_bounced_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for ticket in rows:
        ticket.email_bounced_at = now
    await session.flush()
    if rows:
        log.warning(
            "desk.email.bounced",
            address=address,
            tickets=len(rows),
            channel=str(channel.id),
        )
    return rows[0].issue_id if rows else None


def _record(
    session: AsyncSession,
    channel: EmailChannel,
    parsed: ParsedEmail,
    *,
    issue_id: UUID | None,
    raw_key: str | None,
    skipped: str | None,
) -> EmailMessage:
    row = EmailMessage(
        issue_id=issue_id,
        channel_id=channel.id,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        direction="inbound",
        from_email=parsed.from_email,
        subject=parsed.subject[:998],
        raw_key=raw_key,
        processed_at=utcnow() if skipped is None else None,
        skipped_reason=skipped,
    )
    session.add(row)
    return row


async def _store_raw(
    store: ObjectStore | None, channel: EmailChannel, parsed: ParsedEmail, raw: bytes
) -> str | None:
    """원문을 오브젝트 스토리지에 넣고 키를 돌려준다.

    **실패해도 메일을 버리지 않는다.** 원문 보관은 나중에 돌아보기 위한
    것이고, 스토리지가 잠깐 죽었다고 고객의 요청을 잃는 것은 훨씬 나쁘다.
    """
    if store is None:
        return None
    key = f"email/{channel.id}/{parsed.message_id.strip('<>').replace('/', '_')[:200]}.eml"
    try:
        await store.put(key, raw, content_type="message/rfc822")
    except Exception as exc:
        log.error("desk.email.raw_store_failed", key=key, error=str(exc)[:200])
        return None
    return key


def _sender_actor(email: str) -> Actor:
    """보낸 사람용 액터. `service.py` 의 게스트 액터를 그대로 쓴다.

    **여기서 따로 만들지 않는다.** 게스트 액터에는 "권한 검사에 닿으면 거절
    쪽으로 넘어져야 한다"(`is_customer=True`) 같은 판단이 붙어 있고, 두 벌을
    두면 한쪽만 고쳐지는 날이 온다.
    """
    from ieum.modules.desk.service import guest_actor

    return guest_actor(email)
