"""동시 편집의 **들어오는 문** — 표와 방 잡기 (feature-map B16).

소켓 자체는 브라우저 스펙이 두 창을 띄워 확인한다. 여기서 붙잡는 것은 문이고,
문이 잘못 열리면 그것이 곧 "남이 내 문서를 고칠 수 있다" 다:

- **권한은 표를 낼 때 본다.** 소켓 쪽에서 다시 보면 두 벌이 되고, 두 벌 중
  느슨한 쪽이 문서를 고칠 수 있는 쪽이다.
- **표는 한 번만 쓴다.** 두 번 쓸 수 있으면 URL 이 새는 순간 남의 편집
  세션에 붙을 수 있다.
- **표는 그 문서에만 쓴다.** 문서 A 의 표로 B 를 열 수 있으면 권한 검사가
  통째로 무의미해진다.
- **표는 짧게 산다.** 로그에 남은 표가 쓸모 있는 시간이 있어서는 안 된다.
- 방은 마지막 사람이 나갈 때 닫힌다. 안 닫으면 한 번 열린 문서의 CRDT 상태가
  프로세스에 계속 쌓인다.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import status
from pycrdt import Doc, Text, create_update_message
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from ieum.config import Settings
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.identity.models import User
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.wiki import collab, collab_router
from ieum.modules.wiki import permissions as wiki_perms
from ieum.modules.wiki.collab_router import (
    TICKET_TTL_SECONDS,
    _redeem,
    _ticket_key,
    admit,
)
from ieum.modules.wiki.models import Page, Space
from ieum.modules.wiki.rooms import RoomRegistry
from ieum.modules.wiki.service import PageService
from role_grants import actor_for, grant

BASE = "/api/v1"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "seed-admin-password-1234"
REDIS_URL = "redis://127.0.0.1:6379/0"


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _auth(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post(
        f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _page_over_api(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    space = await client.post(
        f"{BASE}/spaces",
        json={"key": "K" + secrets.token_hex(3).upper(), "name": "Collab"},
        headers=headers,
    )
    assert space.status_code == 201, space.text
    page = await client.post(
        f"{BASE}/pages",
        json={
            "space_id": space.json()["id"],
            "title": "같이 쓰는 문서",
            "body": "처음 본문",
            "publish": True,
        },
        headers=headers,
    )
    assert page.status_code == 201, page.text
    return dict(page.json())


class TestTheTicket:
    async def test_an_editor_gets_a_ticket_and_a_url(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)

        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket"]
        # **경로를 화면이 짜지 않는다.** 서버가 주므로 한쪽만 바뀌는 일이 없다.
        assert body["url"].endswith(f"ticket={body['ticket']}")
        assert f"/pages/{page['id']}/collab?" in body["url"]
        assert body["max_editors"] > 0

    async def test_a_stranger_gets_no_ticket(self, app_client: httpx.AsyncClient) -> None:
        """**권한을 표에서 본다.** 여기서 통과하면 소켓은 표만 보므로,
        이 검사가 곧 편집 권한 검사다."""
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)

        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket")
        assert r.status_code == 401, r.text

    async def test_an_unknown_page_is_not_found(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        r = await app_client.post(f"{BASE}/pages/{uuid4()}/collab-ticket", headers=headers)
        assert r.status_code == 404, r.text

    async def test_the_access_token_is_not_in_the_url(self, app_client: httpx.AsyncClient) -> None:
        """**액세스 토큰을 URL 에 싣지 않는다.**

        URL 은 프록시 로그·리퍼러·브라우저 이력에 남는다. 유효 기간이 남은
        토큰이 로그에 있는 것은 그 자체로 사고이므로, 소켓에는 한 번 쓰는
        표를 실는다.
        """
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        token = headers["Authorization"].removeprefix("Bearer ")

        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)
        assert token not in r.json()["url"]
        assert r.json()["ticket"] != token

    async def test_the_page_is_the_one_asked_for(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        first = await _page_over_api(app_client, headers)
        second = await _page_over_api(app_client, headers)

        r = await app_client.post(f"{BASE}/pages/{first['id']}/collab-ticket", headers=headers)
        holder = await _redeem(REDIS_URL, r.json()["ticket"])
        assert holder is not None
        assert str(holder[0]) == first["id"]
        assert str(holder[0]) != second["id"]


class TestAdmission:
    """소켓이 실제로 보는 문. `admit` 하나가 그 문이다."""

    async def test_a_good_ticket_admits_its_holder(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)

        who = await admit(REDIS_URL, r.json()["ticket"], UUID(page["id"]))
        assert who is not None

    async def test_a_ticket_for_another_page_is_refused(
        self, app_client: httpx.AsyncClient
    ) -> None:
        """**문서 A 의 표로 B 를 열 수 없다.**

        열 수 있으면 권한 검사가 통째로 무의미해진다: 아무 문서 하나에 편집
        권한이 있으면 그 표로 모든 문서에 붙을 수 있다.
        """
        headers = await _auth(app_client)
        mine = await _page_over_api(app_client, headers)
        other = await _page_over_api(app_client, headers)
        r = await app_client.post(f"{BASE}/pages/{mine['id']}/collab-ticket", headers=headers)

        assert await admit(REDIS_URL, r.json()["ticket"], UUID(other["id"])) is None

    async def test_a_made_up_ticket_is_refused(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        assert await admit(REDIS_URL, secrets.token_urlsafe(32), UUID(page["id"])) is None

    async def test_a_ticket_admits_only_once(self, app_client: httpx.AsyncClient) -> None:
        """소켓이 끊겼다 붙을 때 표를 새로 받아야 한다. 재사용되면 URL 이 새는
        순간 남의 편집 세션에 붙을 수 있다."""
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)
        ticket = r.json()["ticket"]

        assert await admit(REDIS_URL, ticket, UUID(page["id"])) is not None
        assert await admit(REDIS_URL, ticket, UUID(page["id"])) is None


class TestRedeemingIt:
    async def test_it_works_once(self, app_client: httpx.AsyncClient) -> None:
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)
        ticket = r.json()["ticket"]

        assert await _redeem(REDIS_URL, ticket) is not None
        # 두 번째는 없다. URL 이 새도 그 표로는 못 붙는다.
        assert await _redeem(REDIS_URL, ticket) is None

    async def test_a_made_up_ticket_opens_nothing(self) -> None:
        assert await _redeem(REDIS_URL, secrets.token_urlsafe(32)) is None

    async def test_a_broken_ticket_body_opens_nothing(self) -> None:
        """Redis 에 이상한 값이 있어도 예외로 터지지 않는다 — 거절이다."""
        client: Redis = redis_from_url(REDIS_URL)  # type: ignore[no-untyped-call]
        ticket = secrets.token_urlsafe(32)
        try:
            await client.set(_ticket_key(ticket), "쓰레기", ex=10)
            assert await _redeem(REDIS_URL, ticket) is None
        finally:
            await client.delete(_ticket_key(ticket))
            await client.aclose()

    async def test_it_expires(self, app_client: httpx.AsyncClient) -> None:
        """**짧게 산다.** 로그에 남은 표가 쓸모 있는 시간이 있어서는 안 된다.

        시계를 기다리지 않는다 — Redis 에 남은 TTL 을 본다. 30초를 세는 시험은
        30초 동안 아무 것도 확인하지 않는다.
        """
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        r = await app_client.post(f"{BASE}/pages/{page['id']}/collab-ticket", headers=headers)

        client: Redis = redis_from_url(REDIS_URL)  # type: ignore[no-untyped-call]
        try:
            ttl = await client.ttl(_ticket_key(r.json()["ticket"]))
        finally:
            await client.aclose()
        assert 0 < ttl <= TICKET_TTL_SECONDS


class TestPermissionOnThePage:
    async def test_a_viewer_without_edit_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """볼 수만 있는 사람은 편집 세션에 못 붙는다.

        `page_for_edit` 하나로 모아 둔 이유가 이것이다 — 소켓이 자기 검사를
        따로 갖고 있으면 이 시험이 그쪽을 안 본다.
        """
        from ieum.core.exceptions import PermissionDeniedError

        space = Space(key=f"V{new_id().hex[-6:].upper()}", name="Read only")
        session.add(space)
        await session.flush()
        page = Page(space_id=space.id, slug="p", title="문서")
        session.add(page)
        viewer = User(email=f"v-{new_id()}@example.com", display_name="구경꾼", status="active")
        session.add(viewer)
        await session.flush()
        await grant(
            session,
            principal_id=viewer.id,
            permissions_granted=(wiki_perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )

        with pytest.raises(PermissionDeniedError):
            await PageService(session, permissions).page_for_edit(actor_for(viewer), page.id)

    async def test_an_editor_gets_the_page(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = Space(key=f"E{new_id().hex[-6:].upper()}", name="Writable")
        session.add(space)
        await session.flush()
        page = Page(space_id=space.id, slug="p", title="문서")
        session.add(page)
        editor = User(email=f"w-{new_id()}@example.com", display_name="편집자", status="active")
        session.add(editor)
        await session.flush()
        await grant(
            session,
            principal_id=editor.id,
            permissions_granted=(wiki_perms.PAGE_EDIT,),
            scope=Scope.space(space.id),
        )

        found = await PageService(session, permissions).page_for_edit(actor_for(editor), page.id)
        assert found.id == page.id


class TestShutdown:
    async def test_shutting_down_saves_open_rooms(
        self, app_client: httpx.AsyncClient, engine: object, settings: Settings
    ) -> None:
        """**내려갈 때 열린 방을 닫는다** — 닫기가 마지막 저장이다.

        소켓이 끊길 때 `release` 가 이미 닫기를 띄우지만 그것을 **기다리는
        사람이 없다**: 프로세스가 내려가면서 루프가 먼저 걷히면 마지막 몇 초의
        편집이 사라진다. 롤링 업데이트는 파드를 하나씩 내리는 일이라 그게 예외가
        아니라 매번이고, 오류도 안 난다 — 사람은 나중에 "내가 쓴 게 없다" 로만
        만난다.

        `RoomRegistry.aclose()` 가 그것을 막으려고 있는 함수인데, **배선을
        빼먹으면 있어도 아무 일도 안 한다.** 그래서 여기서는 레지스트리를
        직접 부르지 않는다 — 실제 앱의 lifespan 을 열고 닫아서 그 배선을 본다.
        """
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])
        me = UUID((await app_client.get("/api/v1/auth/me", headers=headers)).json()["id"])

        from ieum.main import create_app
        from ieum.modules.wiki.rooms import registry

        app = create_app(settings)
        async with app.router.lifespan_context(app):
            # 전역 레지스트리를 쓴다 — lifespan 이 닫는 것이 바로 그것이다.
            room = await registry.acquire(page_id, REDIS_URL)
            got: list[bytes] = []

            async def send(message: bytes) -> None:
                got.append(message)

            # **진짜 사용자로 붙는다.** `saved_by` 가 FK 라서 아무 UUID 나 쓰면
            # 저장이 FK 위반으로 죽고, 이 시험은 배선이 아니라 그 위반을 본다.
            client = collab.Client(user_id=me, send=send)
            await room.join(client)
            await room.handle(client, _typed("내려가기 직전에 친 글"))
            assert "내려가기 직전에 친 글" in room.body()
            # 스냅샷 주기(3초)를 **기다리지 않는다.** 기다리면 주기 저장이
            # 대신 해 주고, 이 시험은 아무것도 안 보게 된다.

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
        async with factory() as session:
            # `page_id` 는 PK 가 아니라 유일 FK 다 — `get()` 으로는 안 찾아진다.
            saved = (
                await session.execute(
                    select(collab.PageCollab).where(collab.PageCollab.page_id == page_id)
                )
            ).scalar_one_or_none()
            assert saved is not None
            body = collab.text_of(saved.state)
        assert "내려가기 직전에 친 글" in body, "내려가면서 마지막 편집이 사라졌다"


def _sessions(engine: object) -> object:
    """시험 엔진에 묶인 세션 팩토리를 내주는 함수.

    레지스트리가 전역 팩토리를 직접 부르지 않게 열어 둔 자리다 — 시험 앱은
    `init_engine()` 을 안 부르고 의존성을 갈아끼우므로 그 전역이 비어 있다.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
    return lambda: factory


class TestTheRegistry:
    async def test_the_second_person_gets_the_same_room(
        self, app_client: httpx.AsyncClient, engine: object
    ) -> None:
        """방이 둘 생기면 같은 프로세스 안에서 프레즌스가 서로를 못 본다."""
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])

        rooms = RoomRegistry(sessions=_sessions(engine))  # type: ignore[arg-type]
        try:
            first = await rooms.acquire(page_id, REDIS_URL)
            second = await rooms.acquire(page_id, REDIS_URL)
            assert first is second
        finally:
            await rooms.aclose()

    async def test_it_closes_when_the_last_person_leaves(
        self, app_client: httpx.AsyncClient, engine: object
    ) -> None:
        """**마지막 하나가 나갈 때만** 닫는다. 먼저 나간 사람에 맞춰 닫으면
        남아 있는 사람의 편집이 통째로 끊긴다."""
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])

        rooms = RoomRegistry(sessions=_sessions(engine))  # type: ignore[arg-type]
        try:
            room = await rooms.acquire(page_id, REDIS_URL)
            await rooms.acquire(page_id, REDIS_URL)

            await rooms.release(page_id)
            assert await rooms.acquire(page_id, REDIS_URL) is room, "한 명 나갔는데 방이 닫혔다"
            await rooms.release(page_id)
            await rooms.release(page_id)

            # 다시 잡으면 **새 방**이다 — 앞의 것은 닫혔다.
            assert await rooms.acquire(page_id, REDIS_URL) is not room
        finally:
            await rooms.aclose()

    async def test_reopening_while_it_closes_waits_for_the_save(
        self, app_client: httpx.AsyncClient, engine: object
    ) -> None:
        """**새로고침이 저장 전의 상태를 심지 않는다.**

        새로고침은 "닫고 곧바로 연다" 다. 닫기는 마지막 저장을 포함하는데 그
        저장을 기다리지 않고 새 방을 심으면, 방금 친 글이 없는 문서가 뜬다 —
        오류 하나 없이. 스냅샷 주기(3초)가 지나지 않은 편집이 정확히 이
        창으로 사라진다.

        여기서는 그 창을 손으로 만든다: 닫기를 태스크로 띄워 방을 등록에서
        빼게만 하고(`sleep(0)`), 곧바로 다시 잡는다.
        """
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])

        rooms = RoomRegistry(sessions=_sessions(engine))  # type: ignore[arg-type]
        try:
            room = await rooms.acquire(page_id, REDIS_URL)
            # 스냅샷은 `saved_by` 를 남기므로 **실제 계정**이어야 한다.
            me = await app_client.get(f"{BASE}/auth/me", headers=headers)
            assert me.status_code == 200, me.text
            typist = collab.Client(user_id=UUID(me.json()["id"]), send=_swallow)
            await room.join(typist)
            await room.handle(typist, _typed("스냅샷 전에 친 글"))
            typed = room.body()
            assert "스냅샷 전에 친 글" in typed

            leaving = asyncio.create_task(rooms.release(page_id))
            await asyncio.sleep(0)  # 등록에서 빠질 틈만 준다
            reopened = await rooms.acquire(page_id, REDIS_URL)
            await leaving

            assert reopened is not room, "닫히던 방을 그대로 돌려줬다"
            # 새 방은 닫히던 방이 들고 있던 것을 그대로 들고 있어야 한다.
            assert reopened.body() == typed, "저장 전의 상태를 심었다"
        finally:
            await rooms.aclose()

    async def test_it_counts_nobody_for_a_room_that_is_not_open(self) -> None:
        assert RoomRegistry().editors(uuid4()) == 0


async def _swallow(_message: bytes) -> None:
    return None


def _typed(text: str) -> bytes:
    """그 글자를 넣는 SYNC_UPDATE 한 통. 클라이언트가 보내는 것과 같은 모양."""
    doc: Doc[Text] = Doc()
    doc[collab.BODY_KEY] = Text()
    before = doc.get_state()
    doc[collab.BODY_KEY] += text
    return create_update_message(doc.get_update(before))


class TestABrokenHandshakeDoesNotLeakTheRoom:
    """**집었으면 반드시 놓는다.**

    방은 참조 세기로 산다. 한 번 안 놓으면 그 문서의 방이 프로세스가 죽을
    때까지 안 없어진다 — 문서 본문과 프레즌스 상태를 통째로 든 채로. 그리고
    그 방은 다음 사람에게 **낡은 상태**를 그대로 내준다.

    `accept()` 는 실제로 실패한다. 붙는 도중에 창을 닫거나 새로고침하면 그
    렇다. 흔한 일이다 — 그래서 새는 자리로 딱 맞다.
    """

    class _WalksAway:
        """핸드셰이크 도중에 가 버린 브라우저."""

        def __init__(self) -> None:
            self.closed_with: int | None = None

        async def accept(self) -> None:
            raise WebSocketDisconnect(code=1006)

        async def close(self, code: int = 1000) -> None:
            self.closed_with = code

        async def send_bytes(self, data: bytes) -> None:
            return None

        async def receive_bytes(self) -> bytes:
            raise WebSocketDisconnect(code=1006)

    async def test_the_room_is_released_when_accept_fails(
        self,
        app_client: httpx.AsyncClient,
        engine: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])

        rooms = RoomRegistry(sessions=_sessions(engine))  # type: ignore[arg-type]
        monkeypatch.setattr(collab_router, "registry", rooms)
        # 표 발급은 여기서 볼 것이 아니다. 보는 것은 잡기와 놓기가 짝이 맞는가다.
        monkeypatch.setattr(collab_router, "admit", _lets_anyone_in)

        try:
            with pytest.raises(WebSocketDisconnect):
                await collab_router.collab_socket(
                    self._WalksAway(),  # type: ignore[arg-type]
                    page_id,
                    Settings(),
                    ticket="a" * 16,
                )

            # 샜으면 세기가 1 로 남아 있다. 그러면 아래에서 한 번 잡았다
            # 놓아도 안 닫히고, 같은 방이 다시 나온다.
            room = await rooms.acquire(page_id, REDIS_URL)
            await rooms.release(page_id)
            assert await rooms.acquire(page_id, REDIS_URL) is not room, "방이 샜다"
        finally:
            await rooms.aclose()

    async def test_a_full_room_is_released_too(
        self,
        app_client: httpx.AsyncClient,
        engine: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """꽉 차서 거절하는 갈래도 똑같이 놓아야 한다."""
        headers = await _auth(app_client)
        page = await _page_over_api(app_client, headers)
        page_id = UUID(page["id"])

        rooms = RoomRegistry(sessions=_sessions(engine))  # type: ignore[arg-type]
        monkeypatch.setattr(collab_router, "registry", rooms)
        monkeypatch.setattr(collab_router, "admit", _lets_anyone_in)
        monkeypatch.setattr(collab.Room, "full", lambda self: True)

        socket = self._WalksAway()
        try:
            await collab_router.collab_socket(
                socket,  # type: ignore[arg-type]
                page_id,
                Settings(),
                ticket="a" * 16,
            )
            assert socket.closed_with == status.WS_1013_TRY_AGAIN_LATER

            room = await rooms.acquire(page_id, REDIS_URL)
            await rooms.release(page_id)
            assert await rooms.acquire(page_id, REDIS_URL) is not room, "방이 샜다"
        finally:
            await rooms.aclose()


async def _lets_anyone_in(redis_url: str, ticket: str, page_id: UUID) -> UUID:
    return new_id()
