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


class MailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, mail: Mail) -> bool:
        """실패해도 예외를 올리지 않는다.

        메일 서버가 죽었다고 이슈 전이가 롤백되면 안 된다. 워커가 이미
        커밋된 이벤트를 처리하는 중이므로, 실패는 로그로 남기고 넘어간다.
        """
        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = mail.to
        message["Subject"] = mail.subject

        body = mail.body
        if mail.link:
            body = f"{body}\n\n{self._settings.base_url.rstrip('/')}{mail.link}"
        message.set_content(body)

        try:
            await aiosmtplib.send(
                message,
                hostname=self._settings.smtp_host,
                port=self._settings.smtp_port,
                start_tls=self._settings.smtp_tls,
                timeout=10,
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
