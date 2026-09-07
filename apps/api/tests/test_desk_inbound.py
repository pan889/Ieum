"""받은 메일이 티켓이 되는 층 (feature-map C6).

`email.py` 는 순수 함수라 원문 바이트를 손으로 적어 시험했다. 여기는 **행을
쓰는 층**이다: 어느 티켓에 붙는지, 안 붙을 때 무엇이 남는지를 실제 DB 로 본다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **주소가 맞을 때만 붙인다.** `In-Reply-To` 는 누구나 적을 수 있다. 헤더만
  믿으면 message-id 를 아는 사람이 남의 티켓에 글을 남기고 **그 티켓의 주인이
  그것을 읽는다.**
- **같은 메일을 두 번 받아도 코멘트가 하나다.** IMAP 은 폴링 중 연결이 끊기면
  같은 메일을 다시 준다.
- **반송은 티켓에 붙이지 않고 주소를 표시한다.** 검증되지 않은 게스트 주소로
  계속 보내면, 남의 주소를 적어 넣은 경우 그 사람에게 계속 배달을 시도한다.
- **자동 응답은 붙이지 않는다.** 붙이면 우리 알림과 고객의 휴가 알림이 서로
  답하는 고리가 생긴다.
- **반영하지 않은 메일도 기록한다.** "왜 이 메일이 안 들어왔나" 의 답이 그것
  뿐이다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.ids import new_id
from ieum.core.permissions import (
    PermissionService,
    Scope,
    set_permission_service,
)
from ieum.modules.desk import inbound
from ieum.modules.desk.email import parse_message
from ieum.modules.desk.models import EmailChannel, EmailMessage, TicketExt
from ieum.modules.desk.service import PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueComment, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

CUSTOMER = "hong@school.example"
DESK = "help@ours.example"

SUMMARY_FIELD = {"key": "summary", "label": "무엇이 문제인가요", "required": True}
BODY_FIELD = {"key": "description", "label": "자세히", "required": False}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


def mail(
    *,
    frm: str = CUSTOMER,
    subject: str = "프린터가 안 됩니다",
    body: str = "3층 프린터가 멈췄습니다.",
    message_id: str | None = None,
    headers: str = "",
) -> bytes:
    lines = [
        f"From: 홍길동 <{frm}>",
        f"To: {DESK}",
        f"Subject: {subject}",
        f"Message-ID: {message_id or f'<{new_id()}@school.example>'}",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
    ]
    if headers:
        lines.append(headers.strip())
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


async def _channel(
    session: AsyncSession, permissions: PermissionService
) -> tuple[EmailChannel, Project]:
    """채널 하나와 그 프로젝트. 포털·요청 유형까지 실제로 만든다."""
    project = Project(key=f"E{new_id().hex[-6:].upper()}", name="Email desk")
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
            form_fields=[SUMMARY_FIELD, BODY_FIELD],
            field_mapping={},
            is_enabled=True,
        )
    ).request_type
    channel = EmailChannel(
        project_id=project.id,
        address=f"help-{new_id().hex[-6:]}@ours.example",
        outbound_from=DESK,
        inbound={"host": "imap.example", "port": 993, "user": "u", "folder": "INBOX"},
        default_request_type_id=request_type.id,
    )
    session.add(channel)
    await session.flush()
    return channel, project


async def _deliver(
    session: AsyncSession,
    permissions: PermissionService,
    channel: EmailChannel,
    raw: bytes,
) -> inbound.InboundResult:
    return await inbound.handle_inbound(
        session, permissions, channel=channel, parsed=parse_message(raw), raw=raw
    )


async def _comments(session: AsyncSession, issue_id: object) -> list[IssueComment]:
    return list(
        (
            await session.execute(
                select(IssueComment).where(IssueComment.issue_id == issue_id)  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )


class TestANewMessage:
    async def test_it_becomes_a_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        channel, project = await _channel(session, permissions)
        result = await _deliver(session, permissions, channel, mail())

        assert result.created is True and result.issue_id is not None
        issue = await session.get(Issue, result.issue_id)
        assert issue is not None
        assert issue.summary == "프린터가 안 됩니다"
        assert issue.project_id == project.id
        ticket = await session.get(TicketExt, result.issue_id)
        assert ticket is not None
        assert ticket.channel == "email"
        assert ticket.guest_email == CUSTOMER
        assert ticket.guest_name == "홍길동"

    async def test_the_body_becomes_the_description(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        channel, _ = await _channel(session, permissions)
        result = await _deliver(session, permissions, channel, mail())
        issue = await session.get(Issue, result.issue_id)
        assert issue is not None and issue.description is not None
        assert "3층 프린터" in issue.description

    async def test_a_message_without_a_subject_still_gets_a_summary(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """비워 두면 목록에서 빈 줄이 된다."""
        channel, _ = await _channel(session, permissions)
        result = await _deliver(session, permissions, channel, mail(subject=""))
        issue = await session.get(Issue, result.issue_id)
        assert issue is not None and issue.summary == inbound.NO_SUBJECT

    async def test_a_known_customer_address_is_still_a_guest_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**메일의 From 은 검증되지 않은 값이다.** 계정에 붙이면 증명 없이
        남의 이름으로 티켓을 만드는 일이 된다 — 게스트 제출과 같은 판단이다."""
        channel, _ = await _channel(session, permissions)
        customer = User(email=CUSTOMER, display_name="홍길동", status="active", is_customer=True)
        session.add(customer)
        await session.flush()

        result = await _deliver(session, permissions, channel, mail())
        ticket = await session.get(TicketExt, result.issue_id)
        assert ticket is not None
        assert ticket.reporter_customer_id is None
        assert ticket.guest_email == CUSTOMER

    async def test_it_records_the_message(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        channel, _ = await _channel(session, permissions)
        result = await _deliver(session, permissions, channel, mail(message_id="<m1@s.example>"))
        row = (
            await session.execute(
                select(EmailMessage).where(EmailMessage.message_id == "<m1@s.example>")
            )
        ).scalar_one()
        assert row.issue_id == result.issue_id
        assert row.direction == "inbound"
        assert row.processed_at is not None
        assert row.skipped_reason is None


class TestThreading:
    async def _thread(
        self, session: AsyncSession, permissions: PermissionService
    ) -> tuple[EmailChannel, object, str]:
        """티켓 하나와, 우리가 보낸 것으로 기록된 message-id."""
        channel, _ = await _channel(session, permissions)
        first = await _deliver(
            session, permissions, channel, mail(message_id="<first@school.example>")
        )
        outbound_id = "<ours-1@ours.example>"
        session.add(
            EmailMessage(
                issue_id=first.issue_id,
                channel_id=channel.id,
                message_id=outbound_id,
                direction="outbound",
                from_email=DESK,
                subject="[X-1] 프린터",
            )
        )
        await session.flush()
        return channel, first.issue_id, outbound_id

    async def test_a_reply_from_the_requester_appends(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        channel, issue_id, outbound_id = await self._thread(session, permissions)
        result = await _deliver(
            session,
            permissions,
            channel,
            mail(body="아직 안 됩니다.", headers=f"In-Reply-To: {outbound_id}"),
        )
        assert result.created is False
        assert result.issue_id == issue_id
        bodies = [c.body for c in await _comments(session, issue_id)]
        assert bodies == ["아직 안 됩니다."]

    async def test_the_appended_comment_is_public_and_has_no_author(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """내부 노트로 들어가면 고객은 자기 회신을 못 본다. 그리고 작성자는
        우리 사용자 중 누구도 아니다 — nil 을 넣으면 FK 가 거절한다."""
        channel, issue_id, outbound_id = await self._thread(session, permissions)
        await _deliver(session, permissions, channel, mail(headers=f"In-Reply-To: {outbound_id}"))
        comment = (await _comments(session, issue_id))[0]
        assert comment.is_internal is False
        assert comment.author_id is None

    async def test_a_reply_from_a_stranger_does_not_append(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 헤더는 누구나 적을 수 있고, 남의
        티켓에 붙은 글은 그 티켓의 주인이 읽는다."""
        channel, issue_id, outbound_id = await self._thread(session, permissions)
        result = await _deliver(
            session,
            permissions,
            channel,
            mail(
                frm="stranger@elsewhere.example",
                body="여기에 끼워 넣습니다",
                headers=f"In-Reply-To: {outbound_id}",
            ),
        )
        # 붙지 않았다.
        assert result.issue_id != issue_id
        assert await _comments(session, issue_id) == []
        # 버리지도 않았다 — 잘못 갈라진 티켓은 상담원이 합칠 수 있다.
        assert result.created is True

    async def test_the_reference_chain_is_also_a_way_home(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`In-Reply-To` 를 지우고 `References` 만 보내는 클라이언트가 있다."""
        channel, issue_id, outbound_id = await self._thread(session, permissions)
        result = await _deliver(
            session,
            permissions,
            channel,
            mail(headers=f"References: <root@x.example> {outbound_id}"),
        )
        assert result.issue_id == issue_id

    async def test_the_subject_key_is_a_fallback(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """헤더를 지우는 클라이언트가 있고, 사람이 옛 메일을 복사해 새로 쓴다."""
        channel, issue_id, _ = await self._thread(session, permissions)
        issue = await session.get(Issue, issue_id)
        assert issue is not None
        project = await session.get(Project, issue.project_id)
        assert project is not None
        key = f"{project.key}-{issue.key_seq}"

        result = await _deliver(session, permissions, channel, mail(subject=f"Re: [{key}] 프린터"))
        assert result.issue_id == issue_id

    async def test_a_subject_key_from_another_project_does_not_match(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """프로젝트를 안 보면 채널을 프로젝트마다 두는 의미가 없어진다."""
        mine, _ = await _channel(session, permissions)
        other_channel, _ = await _channel(session, permissions)
        theirs = await _deliver(session, permissions, other_channel, mail())
        issue = await session.get(Issue, theirs.issue_id)
        assert issue is not None
        project = await session.get(Project, issue.project_id)
        assert project is not None
        key = f"{project.key}-{issue.key_seq}"

        result = await _deliver(session, permissions, mine, mail(subject=f"Re: [{key}] 프린터"))
        assert result.issue_id != theirs.issue_id
        assert result.created is True

    async def test_a_subject_key_pointing_at_a_plain_issue_does_not_match(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """메일로 아무 이슈에 코멘트를 달 수 있게 되면 그건 데스크가 아니라
        우회로다."""
        channel, project = await _channel(session, permissions)
        issue_type = (
            await session.execute(select(IssueType).where(IssueType.project_id == project.id))
        ).scalar_one()
        state = (
            await session.execute(
                select(WorkflowState).where(WorkflowState.workflow_id == issue_type.workflow_id)
            )
        ).scalar_one()
        plain = Issue(
            project_id=project.id,
            type_id=issue_type.id,
            state_id=state.id,
            key_seq=9000,
            summary="티켓이 아닌 이슈",
            priority=3,
        )
        session.add(plain)
        await session.flush()

        result = await _deliver(
            session, permissions, channel, mail(subject=f"Re: [{project.key}-9000] 끼워넣기")
        )
        assert result.issue_id != plain.id
        assert await _comments(session, plain.id) == []


class TestNotAppending:
    async def test_the_same_message_twice_makes_one_comment(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """IMAP 은 폴링 중 연결이 끊기면 같은 메일을 다시 준다."""
        channel, _ = await _channel(session, permissions)
        raw = mail(message_id="<same@school.example>")
        first = await _deliver(session, permissions, channel, raw)
        again = await _deliver(session, permissions, channel, raw)

        assert first.created is True
        assert again.skipped_reason == inbound.SKIP_DUPLICATE
        assert again.issue_id is None
        rows = list(
            (
                await session.execute(
                    select(EmailMessage).where(EmailMessage.message_id == "<same@school.example>")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_an_auto_reply_is_recorded_but_not_appended(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """붙이면 우리 알림과 고객의 휴가 알림이 서로 답하는 고리가 생긴다."""
        channel, _ = await _channel(session, permissions)
        result = await _deliver(
            session,
            permissions,
            channel,
            mail(message_id="<auto@s.example>", headers="Auto-Submitted: auto-replied"),
        )
        assert result.skipped_reason == inbound.SKIP_AUTO_REPLY
        assert result.issue_id is None
        row = (
            await session.execute(
                select(EmailMessage).where(EmailMessage.message_id == "<auto@s.example>")
            )
        ).scalar_one()
        # 기록은 남는다 — "왜 이 메일이 안 들어왔나" 의 답이 이것뿐이다.
        assert row.skipped_reason == inbound.SKIP_AUTO_REPLY
        assert row.processed_at is None

    async def test_an_empty_message_is_skipped(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓을 만들면 상담원이 열어 보고 아무 것도 못 찾는다."""
        channel, _ = await _channel(session, permissions)
        result = await _deliver(session, permissions, channel, mail(body="   "))
        assert result.skipped_reason == inbound.SKIP_NO_BODY
        assert result.issue_id is None


class TestBounces:
    async def _bounce(self, *, address: str) -> bytes:
        boundary = "B"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: text/plain\r\n\r\n"
            "Delivery failed\r\n"
            f"--{boundary}\r\n"
            "Content-Type: message/delivery-status\r\n\r\n"
            "Reporting-MTA: dns; mail.example\r\n\r\n"
            f"Final-Recipient: rfc822; {address}\r\n"
            "Action: failed\r\n"
            "Status: 5.1.1\r\n"
            f"--{boundary}--\r\n"
        )
        return (
            "\r\n".join(
                [
                    "From: MAILER-DAEMON@mail.example",
                    f"To: {DESK}",
                    "Subject: Undelivered Mail Returned to Sender",
                    f"Message-ID: <bounce-{new_id()}@mail.example>",
                    "MIME-Version: 1.0",
                    "Content-Type: multipart/report; report-type=delivery-status; "
                    f'boundary="{boundary}"',
                ]
            )
            + "\r\n\r\n"
            + body
        ).encode()

    async def test_a_bounce_marks_the_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**더 보내지 않는다.** 게스트 주소는 검증되지 않은 값이고, 반송은
        그 주소가 틀렸다는 유일한 신호다."""
        channel, _ = await _channel(session, permissions)
        first = await _deliver(session, permissions, channel, mail())

        result = await _deliver(session, permissions, channel, await self._bounce(address=CUSTOMER))
        assert result.skipped_reason == inbound.SKIP_BOUNCE
        ticket = await session.get(TicketExt, first.issue_id)
        assert ticket is not None and ticket.email_bounced_at is not None

    async def test_a_bounce_does_not_become_a_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """반송이 티켓이 되면 큐에 "Undelivered Mail" 이 쌓인다."""
        channel, _ = await _channel(session, permissions)
        before = len(list((await session.execute(select(TicketExt))).scalars().all()))
        await _deliver(session, permissions, channel, await self._bounce(address=CUSTOMER))
        after = len(list((await session.execute(select(TicketExt))).scalars().all()))
        assert after == before

    async def test_a_bounce_without_an_address_marks_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """짐작해서 아무 주소를 표시하면 멀쩡한 고객에게 메일이 끊긴다."""
        channel, _ = await _channel(session, permissions)
        first = await _deliver(session, permissions, channel, mail())
        raw = (
            "\r\n".join(
                [
                    "From: MAILER-DAEMON@mail.example",
                    f"To: {DESK}",
                    "Subject: failed",
                    f"Message-ID: <b-{new_id()}@mail.example>",
                    'Content-Type: text/plain; charset="utf-8"',
                ]
            )
            + "\r\n\r\n실패했습니다"
        ).encode()

        result = await _deliver(session, permissions, channel, raw)
        assert result.skipped_reason == inbound.SKIP_BOUNCE
        ticket = await session.get(TicketExt, first.issue_id)
        assert ticket is not None and ticket.email_bounced_at is None

    async def test_a_bounce_for_a_different_address_leaves_the_ticket_alone(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        channel, _ = await _channel(session, permissions)
        first = await _deliver(session, permissions, channel, mail())
        await _deliver(
            session, permissions, channel, await self._bounce(address="someone@else.example")
        )
        ticket = await session.get(TicketExt, first.issue_id)
        assert ticket is not None and ticket.email_bounced_at is None
