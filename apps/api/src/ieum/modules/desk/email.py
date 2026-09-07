"""받은 메일을 읽는다 — 순수 함수만 (feature-map C6).

`calendar.py`·`sla.py` 와 같은 이유로 층을 나눈다: 파싱과 정리는 입력과 출력이
전부이므로 손으로 값을 적어 시험할 수 있다. 행을 쓰는 것은 `inbound.py` 다.

**`mailparser` 를 들이지 않았다.** tech-stack.md 는 그것을 적어 두었지만
표준 라이브러리 `email` 이 같은 일을 한다 — RFC 5322 파싱, RFC 2047 헤더
디코딩(한국어 제목이 `=?UTF-8?B?...?=` 로 온다), MIME 트리 순회까지. 의존성
하나가 늘면 그것도 갱신·감사 대상이 되므로, 표준으로 되는 일은 표준으로 한다.
이 판단은 문서에 따로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.policy import default as default_policy
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser


class EmailError(Exception):
    """읽을 수 없는 메일. 부르는 쪽이 보관만 하고 넘어가게 한다."""


@dataclass(frozen=True, slots=True)
class Attachment:
    """메일에 붙어 온 파일 하나."""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """메일 한 통에서 우리가 쓰는 것 전부.

    `body` 는 **표시용**이다(인용과 서명을 지운 것). 원문은 부르는 쪽이
    오브젝트 스토리지에 그대로 넣는다 — 지운 것이 실은 필요했던 경우가 반드시
    생기고, 그때 돌아갈 곳이 있어야 한다.
    """

    message_id: str
    from_email: str
    from_name: str
    subject: str
    body: str
    #: 인용·서명을 지우기 전. 사람이 "왜 이 부분이 사라졌나" 를 물을 때 본다.
    full_body: str
    in_reply_to: str | None = None
    #: `References` 헤더의 message-id 들. 오래된 것부터.
    references: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    #: 반송(bounce)으로 보이는가. 그렇다면 티켓에 붙이지 않고 주소를 표시한다.
    is_bounce: bool = False
    #: 자동 응답(휴가 알림 등)으로 보이는가.
    is_auto_reply: bool = False
    #: 반송이 말하는 "배달 못 한 주소". 없으면 빈 문자열.
    bounced_address: str = ""
    to_emails: tuple[str, ...] = field(default=())


#: 인용의 시작으로 읽는 줄들.
#:
#: 클라이언트마다 다르고, **한국어 클라이언트를 빼면 절반이 안 지워진다** —
#: Gmail 한국어는 "2026년 9월 7일 (월) 오후 3:00, 홍길동 <a@b.c>님이 작성:" 을
#: 쓴다. 영어 패턴만 두면 한국 사용자의 모든 회신에 앞선 대화가 통째로
#: 따라붙는다.
_QUOTE_STARTS = (
    re.compile(r"^\s*-{2,}\s*(original message|forwarded message)\s*-{2,}", re.I),
    re.compile(r"^\s*on .{5,80}\bwrote:\s*$", re.I),
    re.compile(r"^\s*\d{4}년 \d{1,2}월 \d{1,2}일.*작성:\s*$"),
    re.compile(r"^\s*\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\..*(작성|씀):\s*$"),
    re.compile(r"^\s*보낸\s*사람\s*:", re.I),
    re.compile(r"^\s*from\s*:.*<.+@.+>\s*$", re.I),
    re.compile(r"^\s*_{10,}\s*$"),
)

#: 서명 구분선. RFC 3676 의 `-- ` 와, 그것을 안 지키는 클라이언트들.
_SIGNATURE = re.compile(r"^\s*--\s?$|^\s*—\s*$")

#: 자동 응답 헤더. 하나라도 있으면 사람이 쓴 글이 아니다.
_AUTO_HEADERS = (
    ("auto-submitted", lambda v: v.lower() != "no"),
    ("x-auto-response-suppress", lambda _v: True),
    ("x-autoreply", lambda _v: True),
    ("precedence", lambda v: v.lower() in {"bulk", "junk", "auto_reply", "list"}),
)


def parse_message(raw: bytes) -> ParsedEmail:
    """받은 원문을 우리가 쓰는 모양으로. 못 읽으면 `EmailError`.

    **`message_id` 가 없으면 거절한다.** 스레드를 잇는 근거가 그것이고, 없는
    메일을 받아 두면 다음 회신이 어디에도 안 붙는다 — 티켓이 회신마다 하나씩
    새로 생긴다.
    """
    try:
        message = message_from_bytes(raw, policy=default_policy)
    except Exception as exc:  # 손상된 MIME. 원문은 보관하고 여기서 멈춘다.
        raise EmailError(f"MIME 을 읽을 수 없다: {type(exc).__name__}") from exc

    message_id = _header(message, "message-id").strip()
    if not message_id:
        raise EmailError("Message-ID 가 없다")

    from_name, from_email = parseaddr(_header(message, "from"))
    if not from_email:
        raise EmailError("보낸 주소가 없다")

    full_body = _body_of(message)
    bounce, bounced = _bounce_of(message, from_email)
    return ParsedEmail(
        message_id=message_id,
        from_email=from_email.lower(),
        from_name=from_name,
        subject=_header(message, "subject"),
        body=strip_quoted(full_body),
        full_body=full_body,
        in_reply_to=(_header(message, "in-reply-to").strip() or None),
        references=_message_ids(_header(message, "references")),
        attachments=_attachments_of(message),
        is_bounce=bounce,
        is_auto_reply=_is_auto_reply(message),
        bounced_address=bounced,
        to_emails=tuple(
            addr.lower() for _name, addr in getaddresses([_header(message, "to")]) if addr
        ),
    )


def strip_quoted(text: str) -> str:
    """인용과 서명을 지운다. **원문은 부르는 쪽이 보관한다.**

    지우는 이유: 회신이 오갈수록 앞선 대화가 통째로 따라붙어, 티켓 타임라인의
    같은 문장이 열 번 보인다. 상담원은 새로 온 한 문장을 찾으려고 매번
    스크롤한다.

    **첫 줄부터 인용이면 지우지 않는다.** 위쪽에 인용을 두고 아래에 답을 적는
    사람이 있다(하단 인용). 그때 인용을 지우면 본문 전체가 사라진다 — 빈
    코멘트를 만드는 것보다 인용이 남는 쪽이 낫다.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    cut = None
    for index, line in enumerate(lines):
        if _SIGNATURE.match(line) or any(pattern.match(line) for pattern in _QUOTE_STARTS):
            cut = index
            break
        # `>` 로 시작하는 연속 구간도 인용이다. 한 줄만으로는 자르지 않는다 —
        # 마크다운 인용을 쓴 본문과 구별할 수 없다.
        if line.startswith(">") and index + 1 < len(lines) and lines[index + 1].startswith(">"):
            cut = index
            break
    if cut is None:
        return text.strip()
    kept = "\n".join(lines[:cut]).strip()
    return kept if kept else text.strip()


def ticket_key_in(subject: str) -> str | None:
    """제목에서 티켓 키를 찾는다. `[ABC-123] 회신` → `ABC-123`.

    **대괄호 안만 본다.** 어디서나 찾으면 "PROJ-1 관련해서 문의드립니다" 같은
    본론이 남의 티켓 키로 읽힌다. 우리가 보낸 메일의 제목에 대괄호로 넣으므로,
    회신에는 그 모양으로 돌아온다.
    """
    match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", subject)
    return match.group(1) if match else None


def thread_subject(key: str, summary: str) -> str:
    """우리가 보내는 제목. 회신이 키를 갖고 돌아오게 한다."""
    return f"[{key}] {summary}"


# ── 내부 ────────────────────────────────────────────────────────


def _header(message: Message, name: str) -> str:
    """헤더 하나를 사람이 읽는 문자열로.

    `policy.default` 가 대개 디코딩해 주지만, 손으로 만든 메일이나 깨진
    인코딩이 섞이면 `Header` 객체나 원문이 그대로 온다. 한 번 더 통과시킨다 —
    제목에 `=?UTF-8?B?...?=` 가 그대로 보이는 것은 사용자에게 버그다.
    """
    raw = message.get(name)
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


def _message_ids(raw: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in re.finditer(r"<[^<>@\s]+@[^<>\s]+>", raw))


def _body_of(message: Message) -> str:
    """본문. `text/plain` 을 먼저 쓰고, 없으면 HTML 을 글로 바꾼다.

    HTML 만 온 메일을 버리지 않는 이유: 요즘 클라이언트의 기본값이 HTML 이고,
    버리면 그 사람의 요청은 빈 티켓이 된다.
    """
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == "multipart":
            continue
        if _is_attachment(part):
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_text_of(part))
        elif content_type == "text/html":
            html.append(html_to_text(_text_of(part)))
    for candidate in ("\n".join(plain).strip(), "\n".join(html).strip()):
        if candidate:
            return candidate
    return ""


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        content = part.get_payload()
        return content if isinstance(content, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    # 없는 charset 이름을 적어 보내는 클라이언트가 있다. 버리지 않는다.
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _is_attachment(part: Message) -> bool:
    disposition = str(part.get("content-disposition") or "").lower()
    if "attachment" in disposition:
        return True
    # 파일명이 있으면 첨부로 본다. 인라인 이미지가 그렇다 —
    # `Content-Disposition: inline` 이어도 파일이다.
    return bool(part.get_filename())


def _attachments_of(message: Message) -> tuple[Attachment, ...]:
    found: list[Attachment] = []
    for part in message.walk() if message.is_multipart() else []:
        if part.get_content_maintype() == "multipart" or not _is_attachment(part):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        found.append(
            Attachment(
                # 파일명도 RFC 2047 로 인코딩돼 올 수 있다.
                filename=_decoded(part.get_filename() or "attachment"),
                content_type=part.get_content_type(),
                content=payload,
            )
        )
    return tuple(found)


def _decoded(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _is_auto_reply(message: Message) -> bool:
    """자동 응답인가. 붙이면 안 되는 이유는 `inbound.py` 에 적었다."""
    for name, matches in _AUTO_HEADERS:
        value = message.get(name)
        if value is not None and matches(str(value)):
            return True
    return False


def _bounce_of(message: Message, from_email: str) -> tuple[bool, str]:
    """반송인가, 그리고 어느 주소가 배달되지 않았는가.

    셋 중 하나면 반송으로 본다: `multipart/report; report-type=delivery-status`
    (RFC 3462), 빈 반송 경로(`Return-Path: <>`), 보낸 주소가 데몬.

    주소는 `message/delivery-status` 파트의 `Final-Recipient` 에서 읽는다.
    없으면 빈 문자열을 돌려준다 — 짐작해서 아무 주소를 표시하면 멀쩡한 주소로
    메일이 끊긴다.
    """
    report_type = str(message.get_param("report-type") or "").lower()
    looks_like = (
        (message.get_content_type() == "multipart/report" and report_type == "delivery-status")
        or str(message.get("return-path") or "").strip() == "<>"
        or from_email.split("@")[0].lower() in {"mailer-daemon", "postmaster"}
    )
    if not looks_like:
        return False, ""

    for part in message.walk() if message.is_multipart() else []:
        if part.get_content_type() != "message/delivery-status":
            continue
        for block in _delivery_status_blocks(part):
            recipient = block.get("final-recipient") or block.get("original-recipient")
            if recipient:
                # `rfc822; a@b.c` 모양이다.
                return True, parseaddr(recipient.split(";")[-1].strip())[1].lower()
    return True, ""


def _delivery_status_blocks(part: Message) -> list[dict[str, str]]:
    """`message/delivery-status` 안의 필드 묶음들.

    파서가 이 파트를 `Message` 로 주기도 하고 문자열로 주기도 해서 둘 다
    받는다 — 한쪽만 다루면 서버에 따라 반송이 조용히 안 잡힌다.
    """
    payload = part.get_payload()
    if isinstance(payload, list):
        return [
            {key.lower(): str(value) for key, value in block.items()}
            for block in payload
            if isinstance(block, Message)
        ]
    if not isinstance(payload, str):
        return []
    blocks: list[dict[str, str]] = []
    for chunk in payload.split("\n\n"):
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip().lower()] = value.strip()
        if fields:
            blocks.append(fields)
    return blocks


class _TextExtractor(HTMLParser):
    """HTML 을 글로. **블록 요소에서 줄을 바꾼다.**

    태그만 지우면 문단이 한 줄로 붙고, 그러면 인용 잘라내기가 아무 것도 못
    찾는다 — 인용의 시작을 줄 단위로 찾기 때문이다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    _BLOCKS = frozenset(
        {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    )
    #: 내용을 글로 옮기면 안 되는 것들. `<style>` 의 CSS 가 본문에 섞이면
    #: 티켓 첫 줄이 중괄호 덩어리가 된다.
    _SILENT = frozenset({"style", "script", "head"})

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SILENT:
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SILENT:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """HTML 본문을 글로. 서식은 잃되 **줄 구조는 지킨다.**

    마크다운으로 바꾸지 않는 이유: 메일의 HTML 은 표와 인라인 스타일 덩어리이고,
    그것을 마크다운으로 옮기면 원문보다 읽기 어려운 것이 나온다. 여기서 하는
    일은 사람이 읽을 글을 꺼내는 것뿐이다.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    # 빈 줄이 셋 이상 이어지면 둘로 줄인다. HTML 메일은 빈 `<div>` 가 많다.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def build_reply(
    *,
    to: str,
    subject: str,
    body: str,
    from_address: str,
    in_reply_to: str | None,
    references: tuple[str, ...],
) -> EmailMessage:
    """고객에게 보내는 회신 한 통.

    **`In-Reply-To` 와 `References` 를 채운다.** 없으면 고객의 메일함에서 우리
    회신이 새 대화로 뜨고, 고객은 자기가 뭘 물었는지 안 보이는 답을 받는다.
    `References` 는 받은 것에 방금 그 message-id 를 이어 붙인 것이다(RFC 5322).
    """
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        chain = (*references, in_reply_to) if in_reply_to not in references else references
        message["References"] = " ".join(chain)
    message.set_content(body)
    return message
