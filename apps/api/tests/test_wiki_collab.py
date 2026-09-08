"""동시 편집의 방 — pycrdt Y.Text (feature-map B16).

프로토콜 자체는 `pycrdt` 가 갖고 있다. 여기서 붙잡는 것은 **우리가 그 위에
얹은 판단**이고, 틀리면 조용히 틀리는 것들이다:

- **씨 뿌리기가 두 번 일어나면 본문이 두 벌이 된다.** 텍스트 CRDT 는 두 삽입을
  충실히 병합하므로, 아무도 그렇게 편집하지 않은 문서가 만들어진다. 그리고
  그건 예외가 아니라 **그럴듯한 문서**로 나타나서 아무 신호도 없다.
- **프로세스가 여럿이어도 갈라지지 않는다.** 리더가 없어도 되는 이유는 CRDT
  이기 때문이고, 그 성질은 실제로 두 방을 세워 봐야 확인된다.
- **원격에서 온 것을 되돌려 보내지 않는다.** 보내면 두 프로세스가 같은 것을
  영원히 주고받는다.
- **스냅샷은 판을 만들지 않는다.** 만들면 이력이 30초마다 한 줄씩 오염된다.
- **느린 연결 하나가 남의 편집을 밀지 않는다.** 그리고 버렸으면 버렸다고
  말한다 — 조용히 버리면 그 사람 화면만 조용히 뒤처진다.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from pycrdt import Doc, Text, create_awareness_message, create_update_message
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.ids import new_id
from ieum.modules.identity.models import User
from ieum.modules.wiki import collab
from ieum.modules.wiki.models import Page, PageCollab, PageVersion, Space

REDIS_URL = "redis://127.0.0.1:6379/0"


@pytest.fixture
async def redis() -> Any:
    client: Redis = redis_from_url(REDIS_URL)  # type: ignore[no-untyped-call]
    try:
        yield client
    finally:
        await client.aclose()


class Listener:
    """방에 붙은 가짜 사람. 받은 통을 모아 둔다."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.got: list[bytes] = []
        self.client = collab.Client(user_id=user_id, send=self._send)

    async def _send(self, message: bytes) -> None:
        self.got.append(message)

    async def drain(self) -> None:
        """큐에 든 것을 다 흘려보낸다. `pump` 를 태스크로 띄우는 대신 손으로
        비우는 이유: 시험이 "몇 통 왔나" 를 결정적으로 봐야 한다."""
        while not self.client.outbox.empty():
            await self._send(self.client.outbox.get_nowait())


async def _page(session: AsyncSession, body: str = "") -> Page:
    space = Space(key=f"C{new_id().hex[-6:].upper()}", name="Collab")
    session.add(space)
    await session.flush()
    page = Page(space_id=space.id, slug=f"p-{new_id().hex[-6:]}", title="같이 쓰는 문서")
    session.add(page)
    await session.flush()
    if body:
        version = PageVersion(page_id=page.id, number=1, title=page.title, body=body)
        session.add(version)
        await session.flush()
        page.current_version_id = version.id
        await session.flush()
    return page


async def _user(session: AsyncSession) -> User:
    row = User(email=f"e-{new_id()}@example.com", display_name="편집자", status="active")
    session.add(row)
    await session.flush()
    return row


def _merged_text(*states: bytes) -> str:
    """상태 여럿을 한 문서에 겹쳐 본 결과.

    **이것이 실제로 벌어지는 일이다.** 두 프로세스가 각자 심은 상태에서
    출발하면 클라이언트들이 만나는 순간 이렇게 병합된다.
    """
    doc: Doc[Text] = Doc()
    doc[collab.BODY_KEY] = Text()
    for state in states:
        doc.apply_update(state)
    return str(doc[collab.BODY_KEY])


class TestSeeding:
    async def test_the_body_survives_a_round_trip(self, session: AsyncSession) -> None:
        page = await _page(session, "# 제목\n\n본문 한 줄.\n")
        state = await collab.load_or_seed(session, page.id)
        assert collab.text_of(state) == "# 제목\n\n본문 한 줄.\n"

    async def test_a_page_with_no_version_starts_empty(self, session: AsyncSession) -> None:
        """초안만 있는 새 문서도 방을 열 수 있어야 한다. 게시된 판이 없다고
        거절하면 새 문서는 같이 쓸 수 없다."""
        page = await _page(session)
        assert collab.text_of(await collab.load_or_seed(session, page.id)) == ""

    async def test_it_takes_the_winners_state_not_its_own(self, session: AsyncSession) -> None:
        """**이 시험이 이 파일의 이유다.**

        먼저 심은 쪽이 있으면 내가 만든 상태는 버려야 한다. 내 것을 쓰면 두
        프로세스가 서로 다른 문서에서 출발하고, 클라이언트들이 만나는 순간
        본문이 **두 번** 보인다 — 예외도 로그도 없이.
        """
        page = await _page(session, "나중 것")
        # 남이 먼저 심어 둔 상태. 내용이 다르다.
        session.add(PageCollab(page_id=page.id, state=collab.seed_state("먼저 있던 것")))
        await session.commit()

        state = await collab.load_or_seed(session, page.id)

        assert collab.text_of(state) == "먼저 있던 것"
        assert "나중 것" not in collab.text_of(state)

    async def test_two_seedings_do_not_double_the_body(self, session: AsyncSession) -> None:
        """두 번 불러도 겹쳐 놓았을 때 본문이 한 번만 나온다."""
        page = await _page(session, "한 줄뿐이다")
        first = await collab.load_or_seed(session, page.id)
        second = await collab.load_or_seed(session, page.id)

        assert _merged_text(first, second) == "한 줄뿐이다"

    async def test_it_leaves_exactly_one_row(self, session: AsyncSession) -> None:
        page = await _page(session, "본문")
        await collab.load_or_seed(session, page.id)
        await collab.load_or_seed(session, page.id)

        rows = await session.scalar(
            select(func.count()).select_from(PageCollab).where(PageCollab.page_id == page.id)
        )
        assert rows == 1


class TestTheRoom:
    async def test_a_joiner_gets_the_document(self, session: AsyncSession, redis: Redis) -> None:
        page = await _page(session, "이미 있던 본문")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        listener = Listener((await _user(session)).id)
        try:
            await room.join(listener.client)
            await listener.drain()
            # 첫 통은 동기화 요청이다 — 클라이언트가 자기 상태와 견주도록.
            assert listener.got, "붙었는데 아무 것도 안 왔다"
            assert room.body() == "이미 있던 본문"
        finally:
            await room.stop()

    async def test_an_edit_reaches_the_other_person(
        self, session: AsyncSession, redis: Redis
    ) -> None:
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        writer = Listener((await _user(session)).id)
        reader = Listener((await _user(session)).id)
        try:
            await room.join(writer.client)
            await room.join(reader.client)
            await writer.drain()
            await reader.drain()

            await room.handle(writer.client, _typed("가"))
            await reader.drain()

            assert room.body() == "가"
            assert reader.got, "옆 사람에게 아무 것도 안 갔다"
        finally:
            await room.stop()

    async def test_two_processes_converge(self, session: AsyncSession, redis: Redis) -> None:
        """**리더 없이 갈라지지 않는다.**

        프로세스를 둘 흉내 내지 않는다 — 방을 둘 세우고 실제 Redis 로 잇는다.
        이 성질(순서와 무관하게 같은 결과)이 collab 을 별도 서비스로 두지 않은
        이유이므로, 흉내로 확인하면 확인한 것이 없다.
        """
        page = await _page(session, "")
        state = await collab.load_or_seed(session, page.id)
        left = collab.Room(page.id, state, redis)
        right = collab.Room(page.id, state, redis)
        await left.start(_noop_save)
        await right.start(_noop_save)
        try:
            await asyncio.sleep(0.2)  # 구독이 붙을 틈
            a = Listener((await _user(session)).id)
            b = Listener((await _user(session)).id)
            await left.join(a.client)
            await right.join(b.client)

            await left.handle(a.client, _typed("왼쪽"))
            await right.handle(b.client, _typed("오른쪽"))
            await asyncio.sleep(0.5)

            assert left.body() == right.body(), "두 방이 갈라졌다"
            # 둘 다 보존됐다 — 순서는 CRDT 가 정한다.
            assert "왼쪽" in left.body() and "오른쪽" in left.body()
        finally:
            await left.stop()
            await right.stop()

    async def test_a_remote_update_is_not_published_back(
        self, session: AsyncSession, redis: Redis
    ) -> None:
        """되돌려 보내면 두 프로세스가 같은 것을 영원히 주고받는다.

        Redis 채널을 직접 들으면서 **한 번만** 올라오는지 센다.
        """
        page = await _page(session, "")
        state = await collab.load_or_seed(session, page.id)
        left = collab.Room(page.id, state, redis)
        right = collab.Room(page.id, state, redis)
        await left.start(_noop_save)
        await right.start(_noop_save)

        spy: Redis = redis_from_url(REDIS_URL)  # type: ignore[no-untyped-call]
        pubsub = spy.pubsub()
        await pubsub.subscribe(collab.channel_for(page.id))
        seen: list[bytes] = []

        async def watch() -> None:
            async for raw in pubsub.listen():
                if raw.get("type") == "message":
                    seen.append(bytes(raw["data"]))

        task = asyncio.create_task(watch())
        try:
            await asyncio.sleep(0.3)
            a = Listener((await _user(session)).id)
            await left.join(a.client)
            await left.handle(a.client, _typed("한 번"))
            await asyncio.sleep(0.6)

            # 한 번 쳤으면 채널에 한 통이다. 되돌려 보내면 둘 이상이 된다.
            assert len(seen) == 1, f"업데이트가 {len(seen)}번 돌았다"
        finally:
            task.cancel()
            await pubsub.aclose()  # type: ignore[no-untyped-call]
            await spy.aclose()
            await left.stop()
            await right.stop()

    async def test_presence_reaches_the_other_person(
        self, session: AsyncSession, redis: Redis
    ) -> None:
        """커서·프레즌스는 서버가 뜻을 모르고 **중계만** 한다.

        서버가 파싱하면 클라이언트가 프레즌스에 무엇을 담을지 서버에 물어야
        하고, 그러면 색깔 하나 추가에 배포가 필요해진다.
        """
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        a = Listener((await _user(session)).id)
        b = Listener((await _user(session)).id)
        try:
            await room.join(a.client)
            await room.join(b.client)
            await a.drain()
            await b.drain()

            await room.handle(a.client, _presence(a.user_id))
            await b.drain()

            assert b.got, "프레즌스가 옆 사람에게 안 갔다"
        finally:
            await room.stop()

    async def test_a_joiner_learns_who_is_already_here(
        self, session: AsyncSession, redis: Redis
    ) -> None:
        """**먼저 있던 사람이 보여야 한다.**

        각자 자기 상태만 방송하므로, 붙을 때 현재 프레즌스를 주지 않으면 새로
        들어온 사람에게는 빈 방으로 보인다 — 누군가 다음 키를 누를 때까지.
        """
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        first = Listener((await _user(session)).id)
        try:
            await room.join(first.client)
            await first.drain()
            await room.handle(first.client, _presence(first.user_id))

            late = Listener((await _user(session)).id)
            await room.join(late.client)
            await late.drain()

            kinds = {message[0] for message in late.got}
            assert 1 in kinds, "붙었는데 프레즌스를 못 받았다"
        finally:
            await room.stop()


class TestWhenRedisIsGone:
    async def test_editing_keeps_working_without_redis(self, session: AsyncSession) -> None:
        """**Redis 가 없어도 이 프로세스의 편집은 돈다.**

        Redis 는 다른 프로세스로 던지는 전달 수단이고 durable 이 아니다
        (durable 은 `page_collab`). 그래서 못 던지는 것은 "다른 프로세스와
        늦게 만난다" 는 뜻이어야 하고, **여기 붙어 있는 사람들이 못 쓰게 되는
        것**이면 안 된다 — CRDT 는 순서를 안 따지므로 늦게 만나도 결과가 같다.

        죽은 포트를 물려 확인한다. 흉내로 하면 "예외를 삼켰나" 만 보고,
        실제로 편집이 도는지는 안 본다.
        """
        dead: Redis = redis_from_url("redis://127.0.0.1:6390/0")  # type: ignore[no-untyped-call]
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), dead)
        writer = Listener((await _user(session)).id)
        reader = Listener((await _user(session)).id)
        try:
            await room.join(writer.client)
            await room.join(reader.client)
            await writer.drain()
            await reader.drain()

            await room.handle(writer.client, _typed("레디스가 없어도"))
            await asyncio.sleep(0.2)
            await reader.drain()

            assert room.body() == "레디스가 없어도"
            assert reader.got, "같은 프로세스의 옆 사람에게도 안 갔다"
        finally:
            await room.stop()
            await dead.aclose()


class TestSnapshots:
    async def test_it_saves_the_state_not_a_version(self, session: AsyncSession) -> None:
        """**판을 만들지 않는다.** 만들면 이력이 몇 초마다 한 줄씩 오염된다."""
        page = await _page(session, "처음")
        author = await _user(session)
        state = await collab.load_or_seed(session, page.id)

        doc: Doc[Text] = Doc()
        doc[collab.BODY_KEY] = Text()
        doc.apply_update(state)
        doc[collab.BODY_KEY] += " 그리고 더"
        await collab.save_snapshot(session, page.id, doc.get_update(), saved_by=author.id)

        row = (
            await session.execute(select(PageCollab).where(PageCollab.page_id == page.id))
        ).scalar_one()
        assert collab.text_of(row.state) == "처음 그리고 더"
        assert row.saved_by == author.id
        versions = await session.scalar(
            select(func.count()).select_from(PageVersion).where(PageVersion.page_id == page.id)
        )
        assert versions == 1, "스냅샷이 판을 만들었다"

    async def test_the_room_saves_what_was_typed(self, session: AsyncSession, redis: Redis) -> None:
        """방이 실제로 저장을 부르는지. 배선이 끊기면 편집이 몇 초마다 사라진다."""
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        saved: list[tuple[bytes, UUID | None]] = []

        async def remember(state: bytes, who: UUID | None) -> None:
            saved.append((state, who))

        writer = Listener((await _user(session)).id)
        await room.start(remember)
        await room.join(writer.client)
        await room.handle(writer.client, _typed("저장돼야 한다"))
        await room.stop()  # 닫기 전에 한 번 저장한다

        assert saved, "방이 닫혔는데 아무 것도 저장되지 않았다"
        state, who = saved[-1]
        assert collab.text_of(state) == "저장돼야 한다"
        assert who == writer.user_id


class TestBackPressure:
    async def test_a_full_outbox_is_reported(self, session: AsyncSession) -> None:
        """조용히 버리면 그 사람 화면만 조용히 뒤처진다."""
        client = collab.Client(user_id=(await _user(session)).id, send=_swallow)
        while client.offer(b"x"):
            pass
        assert client.outbox.full()
        assert client.offer(b"x") is False

    async def test_a_room_refuses_more_than_the_limit(
        self, session: AsyncSession, redis: Redis
    ) -> None:
        page = await _page(session, "")
        room = collab.Room(page.id, await collab.load_or_seed(session, page.id), redis)
        try:
            for _ in range(collab.MAX_CLIENTS):
                assert not room.full()
                await room.join(Listener((await _user(session)).id).client)
            assert room.full()
        finally:
            await room.stop()


async def _noop_save(state: bytes, who: UUID | None) -> None:
    return None


async def _swallow(message: bytes) -> None:
    return None


def _typed(text: str) -> bytes:
    """그 글자를 넣는 SYNC_UPDATE 한 통. 클라이언트가 보내는 것과 같은 모양."""
    doc: Doc[Text] = Doc()
    doc[collab.BODY_KEY] = Text()
    before = doc.get_state()
    doc[collab.BODY_KEY] += text
    return create_update_message(doc.get_update(before))


def _presence(user_id: UUID) -> bytes:
    """프레즌스 한 통. 서버는 안을 안 보므로 모양만 맞으면 된다."""
    from pycrdt import Awareness

    doc: Doc[Text] = Doc()
    doc[collab.BODY_KEY] = Text()
    awareness = Awareness(doc)
    awareness.set_local_state_field("user", {"id": str(user_id), "name": "편집자"})
    return create_awareness_message(awareness.encode_awareness_update([doc.client_id]))
