"""상담원의 공개 회신이 메일로 나가는 층 (feature-map C6).

메일로 온 티켓은 **메일로 답해야** 대화가 이어진다 — 고객은 포털 주소를
모르고, 알아도 메일에 답하는 것이 그 사람의 습관이다.

붙잡는 것 — 첫째가 이 파일의 자물쇠다:

- **내부 노트는 안 나간다.** 나가면 되돌릴 방법이 없다. 자물쇠가 둘이다:
  이벤트의 `is_internal` 과, 내부 노트에 `None` 을 돌려주는 계약.
- **스레드 헤더를 채운다.** 없으면 고객의 메일함에서 우리 회신이 새 대화로
  뜨고, 고객은 자기가 뭘 물었는지 안 보이는 답을 받는다.
- **우리가 message-id 를 정한다.** 발송 라이브러리에 맡기면 우리가 보낸 id 를
  모르고, 고객의 회신에 그것이 실려 와도 이을 수 없다.
- **반송된 주소로는 안 보낸다.**
- **포털로 온 티켓에는 안 보낸다.** 포털 알림은 `notify` 가 이미 보낸다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.desk import inbound as desk_inbound
from ieum.modules.desk.email import parse_message
from ieum.modules.desk.models import EmailChannel, EmailMessage, TicketExt
from ieum.modules.desk.outbound import OutboundContext, collect_reply_mail
from ieum.modules.desk.service import PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import IssueComment, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

CUSTOMER = "hong@school.example"
DESK = "help@ours.example"
SUMMARY_FIELD = {"key": "summary", "label": "무엇이 문제인가요", "required": True}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


def mail(*, frm: str = CUSTOMER, message_id: str | None = None, headers: str = "") -> bytes:
    lines = [
        f"From: 홍길동 <{frm}>",
        f"To: {DESK}",
        "Subject: 프린터가 안 됩니다",
        f"Message-ID: {message_id or f'<{new_id()}@school.example>'}",
        'Content-Type: text/plain; charset="utf-8"',
    ]
    if headers:
        lines.append(headers.strip())
    return ("\r\n".join(lines) + "\r\n\r\n3층 프린터가 멈췄습니다.").encode()


async def _channel(
    session: AsyncSession, permissions: PermissionService
) -> tuple[EmailChannel, Project]:
    project = Project(key=f"O{new_id().hex[-6:].upper()}", name="Outbound")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
    )
    issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    manager = User(email=f"m-{new_id()}@example.com", display_name="관리자", status="active")
    session.add(manager)
    await session.flush()
    from ieum.modules.desk import permissions as desk_perms

    await grant(
        session,
        principal_id=manager.id,
        permissions_granted=(desk_perms.PORTAL_MANAGE,),
        scope=Scope.project(project.id),
    )
    service = PortalService(session, permissions)
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=f"help-{new_id().hex[-6:]}",
            description=None,
            theme={},
            is_public=True,
        )
    ).portal
    request_type = (
        await service.create_request_type(
            actor_for(manager),
            portal.id,
            issue_type_id=issue_type.id,
            name="Broken thing",
            description=None,
            icon=None,
            position=0,
            form_fields=[SUMMARY_FIELD],
            field_mapping={},
            is_enabled=True,
        )
    ).request_type
    channel = EmailChannel(
        project_id=project.id,
        address=f"help-{new_id().hex[-6:]}@ours.example",
        outbound_from=DESK,
        inbound={"host": "imap.example", "user": "u"},
        default_request_type_id=request_type.id,
    )
    session.add(channel)
    await session.flush()
    return channel, project


async def _email_ticket(
    session: AsyncSession, permissions: PermissionService, *, message_id: str = "<in-1@s.example>"
) -> tuple[EmailChannel, object]:
    channel, _ = await _channel(session, permissions)
    result = await desk_inbound.handle_inbound(
        session,
        permissions,
        channel=channel,
        parsed=parse_message(mail(message_id=message_id)),
        raw=mail(message_id=message_id),
    )
    assert result.issue_id is not None
    return channel, result.issue_id


async def _comment(
    session: AsyncSession, issue_id: object, *, body: str, internal: bool
) -> IssueComment:
    row = IssueComment(
        issue_id=issue_id,  # type: ignore[arg-type]
        author_id=None,
        body=body,
        is_internal=internal,
    )
    session.add(row)
    await session.flush()
    return row


def _envelope(
    issue_id: object, comment: IssueComment, *, internal: bool, actor_id: object = None
) -> EventEnvelope:
    return EventEnvelope(
        id=new_id(),
        event_type="issue.commented",
        aggregate_type="issue",
        aggregate_id=issue_id,  # type: ignore[arg-type]
        payload={
            "issue_key": "OUT-1",
            "comment_id": str(comment.id),
            "is_internal": internal,
            "actor_id": str(actor_id) if actor_id else str(new_id()),
        },
    )


class TestSendingAReply:
    async def test_a_public_reply_goes_out(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        _channel_row, issue_id = await _email_ticket(session, permissions)
        comment = await _comment(session, issue_id, body="고쳤습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        assert len(mails) == 1
        assert mails[0].to == CUSTOMER
        assert mails[0].body == "고쳤습니다."
        # 고객은 자기가 메일을 보낸 그 주소에서 답이 오기를 기대한다.
        assert mails[0].from_address == DESK
        # 제목에 티켓 키가 들어가야 회신이 키를 갖고 돌아온다.
        assert "[OUT-1]" in mails[0].subject

    async def test_it_threads_to_the_last_inbound_message(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**우리가 보낸 것에 답하면** 고객의 메일함에서 우리 회신이 우리
        회신에 이어지고, 고객이 마지막으로 쓴 글과 이어지지 않는다."""
        _channel_row, issue_id = await _email_ticket(
            session, permissions, message_id="<in-1@s.example>"
        )
        comment = await _comment(session, issue_id, body="확인했습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        headers = dict(mails[0].headers)
        assert headers["In-Reply-To"] == "<in-1@s.example>"
        assert "Message-ID" in headers

    async def test_it_records_what_it_sent(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**우리가 message-id 를 정하고 적어 둔다.** 없으면 고객의 회신이
        어디에 붙는지 알 수 없다 — 스레드가 우리 쪽에서 끊긴다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        comment = await _comment(session, issue_id, body="고쳤습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        sent_id = dict(mails[0].headers)["Message-ID"]
        row = (
            await session.execute(select(EmailMessage).where(EmailMessage.message_id == sent_id))
        ).scalar_one()
        assert row.direction == "outbound"
        assert row.issue_id == issue_id
        assert row.from_email == DESK

    async def test_the_recorded_id_lets_the_reply_come_home(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """보낸 것과 받는 것을 **한 바퀴** 돌려 본다. 두 층이 같은 컬럼을
        보는지는 각각의 시험으로는 드러나지 않는다."""
        channel, issue_id = await _email_ticket(session, permissions)
        comment = await _comment(session, issue_id, body="고쳤습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        sent_id = dict(mails[0].headers)["Message-ID"]

        raw = mail(message_id="<reply-1@s.example>", headers=f"In-Reply-To: {sent_id}")
        result = await desk_inbound.handle_inbound(
            session, permissions, channel=channel, parsed=parse_message(raw), raw=raw
        )
        assert result.issue_id == issue_id
        assert result.created is False


class TestNotSending:
    async def test_an_internal_note_never_goes_out(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 나가면 되돌릴 방법이 없다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        comment = await _comment(
            session, issue_id, body="이 고객은 지난번에도 그랬다", internal=True
        )
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=True),
        )
        assert mails == []

    async def test_an_internal_note_does_not_go_out_even_if_the_flag_lies(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**자물쇠가 둘이다.** 페이로드의 플래그가 틀렸더라도 계약이
        내부 노트에 `None` 을 돌려주므로 여기서 막힌다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        comment = await _comment(
            session, issue_id, body="이 고객은 지난번에도 그랬다", internal=True
        )
        # 플래그를 거짓으로 적는다 — 이벤트를 손으로 만들면 실제로 가능하다.
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        assert mails == []

    async def test_a_portal_ticket_gets_no_mail_from_here(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """포털 알림은 `notify` 가 이미 보낸다. 여기서 또 보내면 고객은 왜 이
        주소로 오는지 모른다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        ticket = await session.get(TicketExt, issue_id)
        assert ticket is not None
        ticket.channel = "portal"
        await session.flush()

        comment = await _comment(session, issue_id, body="고쳤습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        assert mails == []

    async def test_a_bounced_address_gets_no_more_mail(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        _channel_row, issue_id = await _email_ticket(session, permissions)
        ticket = await session.get(TicketExt, issue_id)
        assert ticket is not None
        ticket.email_bounced_at = utcnow()
        await session.flush()

        comment = await _comment(session, issue_id, body="고쳤습니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False),
        )
        assert mails == []

    async def test_a_plain_issue_gets_no_mail(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """티켓이 아닌 이슈의 코멘트마다 메일을 보내려 하면 안 된다 —
        아웃박스의 모든 코멘트 이벤트가 이 함수를 지나간다."""
        _, project = await _channel(session, permissions)
        issue_type = (
            await session.execute(select(IssueType).where(IssueType.project_id == project.id))
        ).scalar_one()
        state = (
            await session.execute(
                select(WorkflowState).where(WorkflowState.workflow_id == issue_type.workflow_id)
            )
        ).scalar_one()
        from ieum.modules.issues.models import Issue

        plain = Issue(
            project_id=project.id,
            type_id=issue_type.id,
            state_id=state.id,
            key_seq=7000,
            summary="티켓이 아닌 이슈",
            priority=3,
        )
        session.add(plain)
        await session.flush()
        comment = await _comment(session, plain.id, body="안녕", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(plain.id, comment, internal=False),
        )
        assert mails == []

    async def test_the_customers_own_reply_is_not_echoed_back(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """자기가 보낸 메일이 되돌아오면 무한 고리가 된다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        customer = User(email=CUSTOMER, display_name="홍길동", status="active", is_customer=True)
        session.add(customer)
        await session.flush()
        ticket = await session.get(TicketExt, issue_id)
        assert ticket is not None
        ticket.reporter_customer_id = customer.id
        ticket.guest_email = None
        await session.flush()

        comment = await _comment(session, issue_id, body="아직 안 됩니다.", internal=False)
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings),
            _envelope(issue_id, comment, internal=False, actor_id=customer.id),
        )
        assert mails == []

    async def test_an_unrelated_event_is_ignored_cheaply(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """아웃박스의 **모든** 이벤트가 여기로 온다."""
        _channel_row, issue_id = await _email_ticket(session, permissions)
        envelope = EventEnvelope(
            id=new_id(),
            event_type="issue.transitioned",
            aggregate_type="issue",
            aggregate_id=issue_id,  # type: ignore[arg-type]
            payload={},
        )
        mails = await collect_reply_mail(
            OutboundContext(session=session, settings=settings), envelope
        )
        assert mails == []
