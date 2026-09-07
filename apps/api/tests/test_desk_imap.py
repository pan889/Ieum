"""IMAP 으로 가져오는 층 (feature-map C6).

**진짜 소켓에 붙여 본다.** `imaplib` 을 흉내 내 붙잡을 수 있는 것은 내가 쓴
호출 순서뿐이고, 정작 틀리는 것은 서버가 돌려주는 **응답의 모양**이다
(`_raw_of` 가 그것을 다룬다). 그래서 여기서는 IMAP 을 조금 말하는 서버를
띄우고 실제 클라이언트로 붙는다.

개발 스택에 IMAP 서버를 넣지 못한 사정은 문서에 적었다(mailpit 은 POP3 만
말한다). 그 자리를 이 시험이 대신 메운다 — 브라우저로 몰아 보는 것과 같지는
않지만, "내 코드가 IMAP 대화를 할 수 있는가" 는 여기서 판정된다.

붙잡는 것:

- 응답에서 원문 바이트를 꺼낸다.
- **가져온 것만 읽음으로 표시한다.** 먼저 표시하면 내려받다 끊긴 메일이
  사라진다.
- 한 주기에 가져올 통 수를 지킨다. 밀린 메일함이 워커를 내내 붙잡지 않는다.
- 로그인 실패는 `ImapError` 이고 **비밀번호가 메시지에 없다.**
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from ieum.modules.desk.imap import ImapError, ImapSettings, fetch_unseen

USER = "help"
PASSWORD = "mailbox-secret-1234"


def _message(index: int) -> bytes:
    return (
        f"From: hong{index}@school.example\r\n"
        f"Subject: 문의 {index}\r\n"
        f"Message-ID: <m{index}@school.example>\r\n"
        "\r\n"
        f"본문 {index}\r\n"
    ).encode()


class FakeImap:
    """IMAP 을 **우리가 쓰는 만큼만** 말하는 서버.

    전체를 구현하지 않는다. 우리 코드가 부르는 명령(CAPABILITY·LOGIN·SELECT·
    SEARCH·FETCH·STORE·CLOSE·LOGOUT)만 답한다 — 더 만들면 서버를 시험하는
    셈이 된다.
    """

    def __init__(self, *, count: int = 3, accept_login: bool = True) -> None:
        self.count = count
        self.accept_login = accept_login
        #: 읽음으로 표시된 순번. 표시 순서가 중요하다.
        self.seen: list[int] = []
        #: 원문을 내준 순번.
        self.fetched: list[int] = []
        self._server: asyncio.AbstractServer | None = None
        #: 붙어 있는 연결들. **끊어 주지 않으면 종료가 멈춘다** —
        #: `wait_closed()` 는 핸들러가 끝날 때까지 기다리는데, 클라이언트가
        #: 소켓을 버리고 가면(로그인 실패 뒤가 그렇다) 핸들러는 영원히
        #: `readline()` 에 앉아 있다. 실제로 이 시험이 그렇게 멈췄다.
        self._writers: list[asyncio.StreamWriter] = []
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in self._writers:
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        writer.write(b"* OK fake IMAP ready\r\n")
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                await self._handle(line.decode(errors="replace").strip(), writer)
            except ConnectionError:
                break
        writer.close()

    async def _handle(self, line: str, writer: asyncio.StreamWriter) -> None:
        tag, _, rest = line.partition(" ")
        command, _, args = rest.partition(" ")
        name = command.upper()

        if name == "CAPABILITY":
            writer.write(b"* CAPABILITY IMAP4rev1\r\n")
            writer.write(f"{tag} OK done\r\n".encode())
        elif name == "LOGIN":
            if self.accept_login and PASSWORD in args:
                writer.write(f"{tag} OK logged in\r\n".encode())
            else:
                # 실제 서버가 이런 모양으로 거절한다.
                writer.write(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n".encode())
        elif name == "SELECT":
            writer.write(f"* {self.count} EXISTS\r\n".encode())
            writer.write(f"{tag} OK [READ-WRITE] selected\r\n".encode())
        elif name == "SEARCH":
            ids = " ".join(str(index) for index in range(1, self.count + 1))
            writer.write(f"* SEARCH {ids}\r\n".encode())
            writer.write(f"{tag} OK done\r\n".encode())
        elif name == "FETCH":
            index = int(args.split()[0])
            self.fetched.append(index)
            raw = _message(index)
            writer.write(f"* {index} FETCH (RFC822 {{{len(raw)}}}\r\n".encode())
            writer.write(raw)
            writer.write(b")\r\n")
            writer.write(f"{tag} OK done\r\n".encode())
        elif name == "STORE":
            self.seen.append(int(args.split()[0]))
            writer.write(f"{tag} OK stored\r\n".encode())
        elif name in {"CLOSE", "LOGOUT"}:
            if name == "LOGOUT":
                writer.write(b"* BYE\r\n")
            writer.write(f"{tag} OK done\r\n".encode())
        else:
            writer.write(f"{tag} BAD unsupported: {name}\r\n".encode())
        await writer.drain()


@pytest_asyncio.fixture
async def server() -> AsyncIterator[FakeImap]:
    fake = FakeImap()
    await fake.start()
    yield fake
    await fake.stop()


def _settings(port: int, *, password: str = PASSWORD) -> ImapSettings:
    return ImapSettings(host="127.0.0.1", user=USER, password=password, port=port, use_ssl=False)


class TestFetching:
    async def test_it_brings_back_the_raw_bytes(self, server: FakeImap) -> None:
        """**응답의 모양이 정작 틀리는 자리다.** `{길이}` 리터럴 뒤에 오는
        바이트를 꺼내지 못하면 본문이 없는 티켓만 쌓인다."""
        found = await fetch_unseen(_settings(server.port))
        assert len(found) == 3
        assert b"Message-ID: <m1@school.example>" in found[0]
        assert "본문 1" in found[0].decode()

    async def test_it_marks_only_what_it_took(self, server: FakeImap) -> None:
        """먼저 표시하고 내려받다 끊기면 그 메일은 읽음으로 남아 사라진다."""
        await fetch_unseen(_settings(server.port))
        assert server.fetched == [1, 2, 3]
        assert server.seen == [1, 2, 3]

    async def test_it_respects_the_limit(self, server: FakeImap) -> None:
        """밀린 메일함이 워커를 한 주기 내내 붙잡지 않게 한다."""
        found = await fetch_unseen(_settings(server.port), limit=2)
        assert len(found) == 2
        assert server.fetched == [1, 2]
        # 안 가져온 것은 표시하지 않는다 — 다음 주기가 그것을 집는다.
        assert server.seen == [1, 2]

    async def test_an_empty_mailbox_is_not_an_error(self) -> None:
        fake = FakeImap(count=0)
        await fake.start()
        try:
            assert await fetch_unseen(_settings(fake.port)) == []
        finally:
            await fake.stop()


class TestFailing:
    async def test_a_wrong_password_is_an_imap_error(self, server: FakeImap) -> None:
        with pytest.raises(ImapError) as exc:
            await fetch_unseen(_settings(server.port, password="wrong-one"))
        # **비밀번호가 메시지에 없다.** 이 문자열은 채널의 `last_error` 로
        # 저장되고 화면에 뜬다.
        assert "wrong-one" not in str(exc.value)
        assert "로그인" in str(exc.value)

    async def test_a_closed_port_is_an_imap_error(self) -> None:
        """워커를 죽이지 않는다. 채널 하나가 죽어도 나머지는 돌아야 한다."""
        fake = FakeImap()
        await fake.start()
        port = fake.port
        await fake.stop()
        with pytest.raises(ImapError) as exc:
            await fetch_unseen(_settings(port))
        assert "연결" in str(exc.value)


class TestParsingSettings:
    def test_it_reads_the_stored_shape(self) -> None:
        parsed = ImapSettings.parse(
            {"host": "imap.example", "user": "u", "port": 143, "use_ssl": False}, "pw"
        )
        assert parsed.port == 143
        assert parsed.use_ssl is False

    def test_it_defaults_to_ssl_and_993(self) -> None:
        """평문이 기본이면 비밀번호가 그대로 나간다."""
        parsed = ImapSettings.parse({"host": "imap.example", "user": "u"}, "pw")
        assert parsed.port == 993
        assert parsed.use_ssl is True
        assert parsed.folder == "INBOX"

    @pytest.mark.parametrize(
        "config",
        [{"user": "u"}, {"host": "h"}, {"host": "h", "user": "u", "port": "구백구십삼"}],
    )
    def test_a_config_that_cannot_connect_is_refused(self, config: dict[str, object]) -> None:
        with pytest.raises(ImapError):
            ImapSettings.parse(config, "pw")
