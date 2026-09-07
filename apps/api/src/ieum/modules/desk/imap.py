"""IMAP 으로 메일을 가져온다 (feature-map C6).

**`imaplib` 을 쓰고 스레드에서 돈다.** 표준 라이브러리의 IMAP 은 동기이고,
비동기 대안을 하나 더 들이는 대신 `asyncio.to_thread` 로 감싼다 — 워커는
주기마다 한 번 도는 폴링이라 동시성이 필요한 자리가 아니다.

가져오는 것과 처리하는 것을 나눈다: 여기는 **바이트만** 돌려주고, 티켓으로
만드는 판단은 `inbound.py` 가 한다. 그래야 처리 쪽을 실제 메일 서버 없이
시험할 수 있다.
"""

from __future__ import annotations

import asyncio
import imaplib
from dataclasses import dataclass
from typing import Any

from ieum.core.logging import get_logger

log = get_logger(__name__)

#: 한 주기에 가져올 통 수. 밀린 메일함이 워커를 한 주기 내내 붙잡지 않게 한다.
FETCH_LIMIT = 50

#: 연결·명령 타임아웃(초). 응답 없는 서버가 워커를 영원히 세우지 않게 한다.
TIMEOUT = 20


class ImapError(Exception):
    """가져오지 못했다. 부르는 쪽이 채널에 이유를 적어 화면에 보여 준다."""


@dataclass(frozen=True, slots=True)
class ImapSettings:
    host: str
    user: str
    password: str
    port: int = 993
    folder: str = "INBOX"
    #: 평문 IMAP 을 쓸 것인가. **기본은 아니다** — 비밀번호가 그대로 나간다.
    use_ssl: bool = True

    @classmethod
    def parse(cls, inbound: dict[str, Any], password: str) -> ImapSettings:
        """저장된 설정에서. 모양이 아니면 `ImapError`.

        비밀번호를 **따로 받는다.** 설정 JSONB 에 넣으면 그 사전이 API 응답과
        로그에 그대로 실린다 (models.py 의 `inbound_password_enc` 참고).
        """
        host = str(inbound.get("host") or "").strip()
        user = str(inbound.get("user") or "").strip()
        if not host or not user:
            raise ImapError("호스트와 사용자가 필요하다")
        try:
            port = int(inbound.get("port") or 993)
        except (TypeError, ValueError) as exc:
            raise ImapError("포트가 숫자가 아니다") from exc
        return cls(
            host=host,
            user=user,
            password=password,
            port=port,
            folder=str(inbound.get("folder") or "INBOX"),
            use_ssl=bool(inbound.get("use_ssl", True)),
        )


async def fetch_unseen(settings: ImapSettings, *, limit: int = FETCH_LIMIT) -> list[bytes]:
    """읽지 않은 메일의 원문들. 가져온 것은 **읽음으로 표시한다.**

    표시하지 않으면 다음 주기가 같은 메일을 다시 가져온다. `inbound.py` 의
    중복 가드가 그때도 티켓을 하나로 유지하지만, 매 주기 메일함 전체를
    내려받는 것은 그것과 별개의 낭비다.

    **표시는 가져온 뒤에 한다.** 먼저 표시하고 내려받다가 끊기면 그 메일은
    영원히 안 읽힌 것으로 남지 않고 — 읽음으로 남아 사라진다.
    """
    return await asyncio.to_thread(_fetch, settings, limit)


def _fetch(settings: ImapSettings, limit: int) -> list[bytes]:
    client = _connect(settings)
    try:
        status, _ = client.select(settings.folder)
        if status != "OK":
            raise ImapError(f"폴더를 열 수 없다: {settings.folder}")
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            raise ImapError("검색이 실패했다")
        # 응답은 bytes 인데 `fetch`·`store` 는 str 을 받는다. 한 번에 옮긴다.
        ids = [uid.decode(errors="replace") for uid in (data[0] or b"").split()][:limit]
        found: list[bytes] = []
        for uid in ids:
            status, payload = client.fetch(uid, "(RFC822)")
            if status != "OK" or not payload:
                log.warning("desk.imap.fetch_failed", uid=uid)
                continue
            raw = _raw_of(payload)
            if raw is None:
                continue
            found.append(raw)
            # **가져온 것만** 표시한다. 먼저 표시하고 내려받다가 끊기면 그
            # 메일은 읽음으로 남아 사라진다.
            client.store(uid, "+FLAGS", "\\Seen")
        return found
    finally:
        _close(client)


def _connect(settings: ImapSettings) -> imaplib.IMAP4:
    client: imaplib.IMAP4 | None = None
    try:
        if settings.use_ssl:
            client = imaplib.IMAP4_SSL(settings.host, settings.port, timeout=TIMEOUT)
        else:
            # 평문은 설정에서 명시적으로 켜야만 쓴다. 비밀번호가 그대로 나가
            # 므로 개발 스택이나 사내망 말고는 쓸 자리가 없다.
            client = imaplib.IMAP4(settings.host, settings.port, timeout=TIMEOUT)
        client.login(settings.user, settings.password)
    except imaplib.IMAP4.error as exc:
        # **붙은 소켓을 닫고 올린다.** 안 닫으면 로그인이 실패할 때마다 연결이
        # 하나씩 샌다 — 폴링은 15초마다 돌므로 비밀번호가 틀린 채널 하나가
        # 하룻밤에 오천 개를 남긴다. 시험을 붙이다 드러났다: 가짜 서버의
        # 핸들러가 끊기지 않아 종료가 멈췄다.
        _close(client)
        # **비밀번호를 로그에 남기지 않는다.** 예외 문자열에 서버가 보낸 응답이
        # 들어오는데, 로그인 실패 응답에 사용자 이름이 섞이는 서버가 있다.
        raise ImapError(f"로그인할 수 없다: {type(exc).__name__}") from exc
    except OSError as exc:
        _close(client)
        raise ImapError(f"연결할 수 없다: {type(exc).__name__}") from exc
    return client


def _close(client: imaplib.IMAP4 | None) -> None:
    """닫기가 실패해도 넘어간다. 이미 가져온 메일을 잃을 이유가 없다.

    **삼키지 않고 로그로 남긴다.** 닫기가 매번 실패하는 서버는 연결이 새고
    있다는 신호이고, 조용히 지나가면 그 사실을 알 방법이 없다.

    로그인 전에 실패하면 `client` 가 없을 수 있다 — 그때는 할 일이 없다.
    """
    if client is None:
        return
    for step, action in (("close", client.close), ("logout", client.logout)):
        try:
            action()
        except Exception as exc:
            log.debug("desk.imap.close_failed", step=step, error=type(exc).__name__)


def _raw_of(payload: list[Any]) -> bytes | None:
    """`fetch` 응답에서 원문 바이트를 꺼낸다.

    응답 모양이 서버마다 다르다: `[(b'1 (RFC822 {123}', b'...raw...'), b')']`
    가 흔하지만 튜플이 여러 개 오기도 한다. **첫 바이트 덩어리**를 쓴다.
    """
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None
