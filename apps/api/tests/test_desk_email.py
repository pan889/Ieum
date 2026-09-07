"""받은 메일을 읽는 층 (feature-map C6).

순수 함수라 **원문 바이트를 손으로 적어** 시험한다. 실제로 오는 메일을 흉내
내는 것이 요점이므로, 사전을 만들어 넘기지 않고 RFC 5322 문장을 그대로 쓴다.

붙잡는 것:

- **한국어 제목과 본문이 깨지지 않는다.** `=?UTF-8?B?...?=` 가 제목에 그대로
  보이는 것은 사용자에게 버그다.
- **인용과 서명을 지운다.** 회신이 오갈수록 앞선 대화가 통째로 따라붙어 같은
  문장이 열 번 보인다.
- **한국어 클라이언트의 인용 표시도 안다.** 영어 패턴만 두면 한국 사용자의
  모든 회신에 앞선 대화가 남는다.
- **`Message-ID` 가 없으면 거절한다.** 스레드를 잇는 근거가 그것이다.
- **반송과 자동 응답은 구별한다.** 붙이면 고객이 자기 휴가 알림을 티켓에서
  보게 된다.
"""

from __future__ import annotations

import pytest

from ieum.modules.desk.email import (
    EmailError,
    build_reply,
    html_to_text,
    parse_message,
    strip_quoted,
    thread_subject,
    ticket_key_in,
)


def mail(
    *,
    body: str = "프린터가 안 됩니다",
    subject: str = "문의",
    headers: str = "",
    message_id: str = "<a1@example.com>",
    content_type: str = 'text/plain; charset="utf-8"',
) -> bytes:
    lines = [
        "From: 홍길동 <hong@school.example>",
        "To: help@ours.example",
        f"Subject: {subject}",
        f"Message-ID: {message_id}",
        "MIME-Version: 1.0",
        f"Content-Type: {content_type}",
    ]
    if headers:
        lines.append(headers.strip())
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


class TestReadingAMessage:
    def test_it_reads_the_parts_we_use(self) -> None:
        parsed = parse_message(mail())
        assert parsed.message_id == "<a1@example.com>"
        assert parsed.from_email == "hong@school.example"
        assert parsed.from_name == "홍길동"
        assert parsed.subject == "문의"
        assert parsed.body == "프린터가 안 됩니다"
        assert parsed.to_emails == ("help@ours.example",)

    def test_it_lowercases_the_sender(self) -> None:
        """주소로 스레드 주인을 확인하므로 대소문자로 갈리면 안 된다."""
        raw = mail().replace(b"hong@school.example", b"Hong@School.Example")
        assert parse_message(raw).from_email == "hong@school.example"

    def test_an_encoded_korean_subject_comes_out_readable(self) -> None:
        # 실제로 오는 모양이다. 디코딩을 안 하면 제목에 이 글자가 그대로 뜬다.
        # (base64 를 손으로 적었다가 한 글자를 틀렸고, 코드가 맞았다.)
        parsed = parse_message(mail(subject="=?UTF-8?B?7ZWc6rWt7Ja0IOygnOuqqQ==?="))
        assert parsed.subject == "한국어 제목"

    def test_a_message_without_an_id_is_refused(self) -> None:
        """받아 두면 다음 회신이 어디에도 안 붙어 티켓이 회신마다 하나씩
        새로 생긴다."""
        raw = b"\r\n".join(
            [b"From: a@b.c", b"Subject: x", b"", b"body"],
        )
        with pytest.raises(EmailError):
            parse_message(raw)

    def test_a_message_without_a_sender_is_refused(self) -> None:
        raw = b"\r\n".join([b"Message-ID: <x@y>", b"Subject: x", b"", b"body"])
        with pytest.raises(EmailError):
            parse_message(raw)

    def test_it_reads_the_thread_headers(self) -> None:
        parsed = parse_message(
            mail(
                headers="In-Reply-To: <first@ours.example>\r\n"
                "References: <root@ours.example> <first@ours.example>"
            )
        )
        assert parsed.in_reply_to == "<first@ours.example>"
        assert parsed.references == ("<root@ours.example>", "<first@ours.example>")

    def test_an_unknown_charset_does_not_lose_the_body(self) -> None:
        """없는 charset 이름을 적어 보내는 클라이언트가 있다."""
        parsed = parse_message(mail(content_type='text/plain; charset="x-unknown"'))
        assert "프린터" in parsed.body


class TestPickingTheBody:
    def _multipart(self, *, plain: str | None, html: str | None) -> bytes:
        parts = []
        if plain is not None:
            parts.append(
                'Content-Type: text/plain; charset="utf-8"\r\n'
                "Content-Transfer-Encoding: 8bit\r\n\r\n" + plain
            )
        if html is not None:
            parts.append(
                'Content-Type: text/html; charset="utf-8"\r\n'
                "Content-Transfer-Encoding: 8bit\r\n\r\n" + html
            )
        boundary = "BOUND"
        body = "".join(f"--{boundary}\r\n{part}\r\n" for part in parts) + f"--{boundary}--\r\n"
        return mail(body=body, content_type=f'multipart/alternative; boundary="{boundary}"')

    def test_plain_text_wins(self) -> None:
        raw = self._multipart(plain="글로 온 본문", html="<p>HTML 로 온 본문</p>")
        assert parse_message(raw).body == "글로 온 본문"

    def test_html_only_is_not_thrown_away(self) -> None:
        """요즘 클라이언트의 기본값이 HTML 이다. 버리면 그 사람의 요청은 빈
        티켓이 된다."""
        raw = self._multipart(plain=None, html="<p>프린터가</p><p>안 됩니다</p>")
        assert parse_message(raw).body == "프린터가\n\n안 됩니다"


class TestHtmlToText:
    def test_blocks_become_line_breaks(self) -> None:
        """태그만 지우면 문단이 한 줄로 붙고, 그러면 인용 잘라내기가 아무 것도
        못 찾는다 — 인용의 시작을 줄 단위로 찾기 때문이다."""
        assert html_to_text("<div>첫 줄</div><div>둘째 줄</div>") == "첫 줄\n\n둘째 줄"

    def test_style_and_script_do_not_leak_into_the_body(self) -> None:
        """`<style>` 의 CSS 가 섞이면 티켓 첫 줄이 중괄호 덩어리가 된다."""
        html = "<head><style>.x{color:red}</style></head><body><p>본문</p></body>"
        assert html_to_text(html) == "본문"

    def test_entities_come_out_as_characters(self) -> None:
        assert html_to_text("<p>a &amp; b &lt;c&gt;</p>") == "a & b <c>"

    def test_br_breaks_the_line(self) -> None:
        assert html_to_text("한 줄<br>다음 줄") == "한 줄\n다음 줄"


class TestStrippingQuotes:
    def test_it_cuts_at_the_english_marker(self) -> None:
        text = "고쳤습니다.\n\nOn Mon, 7 Sep 2026 at 15:00, 홍길동 <a@b.c> wrote:\n> 문의합니다"
        assert strip_quoted(text) == "고쳤습니다."

    def test_it_cuts_at_the_korean_marker(self) -> None:
        """**이것이 이 함수의 이유다.** 영어 패턴만 두면 한국 사용자의 모든
        회신에 앞선 대화가 통째로 남는다."""
        text = (
            "확인했습니다.\n\n"
            "2026년 9월 7일 (월) 오후 3:00, 홍길동 <a@b.c>님이 작성:\n"
            "> 프린터가 안 됩니다"
        )
        assert strip_quoted(text) == "확인했습니다."

    def test_it_cuts_at_the_outlook_marker(self) -> None:
        text = "네.\n\n-----Original Message-----\nFrom: 홍길동\n프린터가"
        assert strip_quoted(text) == "네."

    def test_it_cuts_at_the_signature_delimiter(self) -> None:
        text = "확인했습니다.\n\n-- \n홍길동\n단비교육"
        assert strip_quoted(text) == "확인했습니다."

    def test_it_cuts_a_run_of_quoted_lines(self) -> None:
        text = "네.\n> 앞선 말\n> 그 다음 말"
        assert strip_quoted(text) == "네."

    def test_one_quoted_line_alone_is_kept(self) -> None:
        """마크다운 인용을 쓴 본문과 구별할 수 없다."""
        text = "이렇게 적혀 있습니다:\n> 오류 코드 5\n확인 부탁드립니다."
        assert strip_quoted(text) == text

    def test_a_top_quoted_reply_is_not_emptied(self) -> None:
        """**위쪽에 인용을 두고 아래에 답을 적는 사람이 있다.** 그때 인용을
        지우면 본문 전체가 사라진다 — 빈 코멘트를 만드는 것보다 인용이 남는
        쪽이 낫다."""
        text = "> 프린터가 안 됩니다\n> 확인 부탁드립니다\n\n네, 고쳤습니다."
        assert strip_quoted(text) == text

    def test_the_full_body_is_kept_alongside(self) -> None:
        """사람이 "왜 이 부분이 사라졌나" 를 물을 때 돌아갈 곳이 있어야 한다."""
        parsed = parse_message(mail(body="네.\n\n-- \n홍길동"))
        assert parsed.body == "네."
        assert "홍길동" in parsed.full_body


class TestTicketKeyInSubject:
    def test_it_finds_the_key_in_brackets(self) -> None:
        assert ticket_key_in("[ABC-123] 회신") == "ABC-123"
        assert ticket_key_in("Re: [ABC-123] 회신") == "ABC-123"

    def test_it_ignores_a_key_outside_brackets(self) -> None:
        """**어디서나 찾으면** "PROJ-1 관련해서 문의드립니다" 같은 본론이 남의
        티켓 키로 읽힌다."""
        assert ticket_key_in("PROJ-1 관련 문의") is None

    def test_our_own_subject_round_trips(self) -> None:
        assert ticket_key_in(thread_subject("ABC-9", "프린터")) == "ABC-9"


class TestAttachments:
    def test_it_takes_the_file_and_its_name(self) -> None:
        boundary = "B"
        body = (
            f"--{boundary}\r\n"
            'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
            "사진 보냅니다\r\n"
            f"--{boundary}\r\n"
            "Content-Type: image/png\r\n"
            'Content-Disposition: attachment; filename="=?UTF-8?B?7IKs7KeELnBuZw==?="\r\n'
            "Content-Transfer-Encoding: base64\r\n\r\n"
            "aGVsbG8=\r\n"
            f"--{boundary}--\r\n"
        )
        parsed = parse_message(
            mail(body=body, content_type=f'multipart/mixed; boundary="{boundary}"')
        )
        assert parsed.body == "사진 보냅니다"
        assert len(parsed.attachments) == 1
        # 파일명도 RFC 2047 로 인코딩돼 온다.
        assert parsed.attachments[0].filename == "사진.png"
        assert parsed.attachments[0].content == b"hello"

    def test_an_inline_image_is_still_a_file(self) -> None:
        """`Content-Disposition: inline` 이어도 파일이다. 본문으로 읽으면
        티켓에 바이트가 쏟아진다."""
        boundary = "B"
        body = (
            f"--{boundary}\r\n"
            'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
            "보세요\r\n"
            f"--{boundary}\r\n"
            "Content-Type: image/png\r\n"
            'Content-Disposition: inline; filename="logo.png"\r\n'
            "Content-Transfer-Encoding: base64\r\n\r\n"
            "aGVsbG8=\r\n"
            f"--{boundary}--\r\n"
        )
        parsed = parse_message(
            mail(body=body, content_type=f'multipart/mixed; boundary="{boundary}"')
        )
        assert parsed.body == "보세요"
        assert [a.filename for a in parsed.attachments] == ["logo.png"]


class TestBouncesAndAutoReplies:
    def test_a_delivery_status_report_is_a_bounce(self) -> None:
        boundary = "B"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: text/plain\r\n\r\n"
            "Delivery failed\r\n"
            f"--{boundary}\r\n"
            "Content-Type: message/delivery-status\r\n\r\n"
            "Reporting-MTA: dns; mail.example\r\n\r\n"
            "Final-Recipient: rfc822; Wrong@School.Example\r\n"
            "Action: failed\r\n"
            "Status: 5.1.1\r\n"
            f"--{boundary}--\r\n"
        )
        raw = mail(
            body=body,
            content_type=f'multipart/report; report-type=delivery-status; boundary="{boundary}"',
        )
        parsed = parse_message(raw)
        assert parsed.is_bounce is True
        # 어느 주소가 죽었는지 알아야 그 주소로 그만 보낼 수 있다.
        assert parsed.bounced_address == "wrong@school.example"

    def test_a_daemon_sender_is_a_bounce_even_without_a_report(self) -> None:
        raw = mail().replace(
            b"From: \xed\x99\x8d\xea\xb8\xb8\xeb\x8f\x99 <hong@school.example>",
            b"From: MAILER-DAEMON@mail.example",
        )
        parsed = parse_message(raw)
        assert parsed.is_bounce is True
        # 짐작해서 아무 주소를 표시하면 멀쩡한 주소로 메일이 끊긴다.
        assert parsed.bounced_address == ""

    def test_an_empty_return_path_is_a_bounce(self) -> None:
        assert parse_message(mail(headers="Return-Path: <>")).is_bounce is True

    def test_an_ordinary_message_is_not_a_bounce(self) -> None:
        assert parse_message(mail()).is_bounce is False

    @pytest.mark.parametrize(
        "headers",
        [
            "Auto-Submitted: auto-replied",
            "X-Autoreply: yes",
            "Precedence: bulk",
            "X-Auto-Response-Suppress: All",
        ],
    )
    def test_an_auto_reply_is_marked(self, headers: str) -> None:
        assert parse_message(mail(headers=headers)).is_auto_reply is True

    def test_auto_submitted_no_is_a_real_message(self) -> None:
        """RFC 3834 는 사람이 쓴 메일에 `no` 를 적게 한다. 값을 안 보면
        멀쩡한 회신이 자동 응답으로 버려진다."""
        assert parse_message(mail(headers="Auto-Submitted: no")).is_auto_reply is False


class TestBuildingAReply:
    def test_it_threads_the_reply(self) -> None:
        """**`In-Reply-To` 가 없으면** 고객의 메일함에서 우리 회신이 새 대화로
        뜨고, 고객은 자기가 뭘 물었는지 안 보이는 답을 받는다."""
        message = build_reply(
            to="hong@school.example",
            subject="[ABC-1] 프린터",
            body="고쳤습니다.",
            from_address="help@ours.example",
            in_reply_to="<a1@example.com>",
            references=("<root@ours.example>",),
        )
        assert message["In-Reply-To"] == "<a1@example.com>"
        assert message["References"] == "<root@ours.example> <a1@example.com>"
        assert message["To"] == "hong@school.example"

    def test_a_first_message_has_no_thread_headers(self) -> None:
        message = build_reply(
            to="hong@school.example",
            subject="[ABC-1] 프린터",
            body="접수했습니다.",
            from_address="help@ours.example",
            in_reply_to=None,
            references=(),
        )
        assert message["In-Reply-To"] is None
        assert message["References"] is None

    def test_it_does_not_repeat_an_id_already_in_references(self) -> None:
        message = build_reply(
            to="a@b.c",
            subject="s",
            body="b",
            from_address="help@ours.example",
            in_reply_to="<a1@example.com>",
            references=("<a1@example.com>",),
        )
        assert message["References"] == "<a1@example.com>"
