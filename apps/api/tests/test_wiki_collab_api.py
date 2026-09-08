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

import secrets
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.identity.models import User
from ieum.modules.org.repository import OrgPermissionResolver
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

    async def test_it_counts_nobody_for_a_room_that_is_not_open(self) -> None:
        assert RoomRegistry().editors(uuid4()) == 0
