"""메일 발송.

수신자 언어로 렌더한 문구를 받아 보내기만 한다. 문구를 만들지 않는다 —
그건 서비스의 일이고, 여기가 문구를 알면 i18n 이 두 곳으로 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from ieum.config import Settings
from ieum.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Mail:
    to: str
    subject: str
    body: str
    #: 앱 안의 경로. 본문 끝에 절대 URL 로 붙인다.
    link: str | None = None
    #: 이 통만 다른 From 으로 보낸다. 없으면 설정의 `mail_from`.
    #:
    #: 데스크의 메일 채널(C6)이 쓴다: 고객은 자기가 메일을 보낸 그 주소에서
    #: 답이 오기를 기대하고, 우리 알림 주소에서 오면 회신이 그쪽으로 간다 —
    #: 그 주소는 폴링하지 않으므로 회신이 사라진다.
    from_address: str | None = None
    #: 추가 헤더. `In-Reply-To`·`References`·`Message-ID` 가 여기로 온다.
    #:
    #: **발송 경로를 둘로 만들지 않으려고** 여기에 둔다. 데스크가 자기
    #: aiosmtplib 호출을 따로 가지면 타임아웃·TLS·실패 처리가 두 벌이 되고,
    #: 한쪽만 고쳐지는 날이 온다.
    headers: tuple[tuple[str, str], ...] = ()


class MailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, mail: Mail) -> bool:
        """실패해도 예외를 올리지 않는다.

        메일 서버가 죽었다고 이슈 전이가 롤백되면 안 된다. 워커가 이미
        커밋된 이벤트를 처리하는 중이므로, 실패는 로그로 남기고 넘어간다.
        """
        message = EmailMessage()
        message["From"] = mail.from_address or self._settings.mail_from
        message["To"] = mail.to
        message["Subject"] = mail.subject
        for name, value in mail.headers:
            message[name] = value

        body = mail.body
        if mail.link:
            body = f"{body}\n\n{self._settings.base_url.rstrip('/')}{mail.link}"
        message.set_content(body)

        try:
            # 인증은 **값이 있을 때만** 건다. 빈 사용자로 AUTH 를 시도하면
            # 인증을 안 요구하는 릴레이(개발의 mailpit 이 그렇다)가 거절한다.
            #
            # `username=None` 을 넘기는 것으로 "안 건다" 를 표현한다.
            # `**{}` 로 키를 빼면 mypy 가 나머지 키워드까지 그 딕셔너리로
            # 받는다고 보고 엉뚱한 자리(ssl 컨텍스트·소켓)에 맞춰 본다.
            user = self._settings.smtp_user or None
            await aiosmtplib.send(
                message,
                hostname=self._settings.smtp_host,
                port=self._settings.smtp_port,
                start_tls=self._settings.smtp_tls,
                timeout=10,
                username=user,
                password=self._settings.smtp_password.get_secret_value() if user else None,
            )
        except Exception as exc:
            log.error(
                "mail.send_failed",
                to=mail.to,
                subject=mail.subject,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False
        log.info("mail.sent", to=mail.to, subject=mail.subject)
        return True


async def send_all(settings: Settings, mails: list[Mail]) -> int:
    """여러 통을 보내고 성공 수를 돌려준다.

    세션을 받지 않는다. 발송은 DB 와 무관하고, 트랜잭션 밖에서 도는 게
    맞다 — SMTP 가 느리면 커넥션을 붙잡고 락이 쌓인다.
    """
    sender = MailSender(settings)
    sent = 0
    for mail in mails:
        if await sender.send(mail):
            sent += 1
    return sent
