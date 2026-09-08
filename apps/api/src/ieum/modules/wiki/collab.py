"""동시 편집 — pycrdt Y.Text 기반 공유 방 (B16, M5).

## 왜 방이 필요한가

마크다운이 정본이다(ADR-0008). 두 사람이 같은 문서를 고치면 낙관적 잠금은
**나중에 저장한 사람을 거절한다** — 맞는 동작이지만, 같이 쓰는 자리에서는
거절이 답이 아니다. 텍스트 CRDT 는 두 편집을 다 보존한다.

## 씨 뿌리기가 이 설계에서 가장 위험한 자리다

방을 처음 세울 때 지금 게시된 본문을 Y.Text 에 넣어야 한다. 그런데 프로세스가
둘이면 **둘이 각각 넣는다.** 텍스트 CRDT 는 두 삽입을 충실히 병합하므로 결과는
본문이 **두 번** 들어간 문서다 — 아무도 그렇게 편집하지 않았는데.

잠금으로 막지 않는다. `page_collab.page_id` 에 걸린 유일 제약이 이미 답이다:

1. 게시된 본문으로 상태를 하나 만들어 `INSERT ... ON CONFLICT DO NOTHING`.
2. **행을 다시 읽는다.** 이긴 쪽의 상태가 거기 있다.
3. 그 상태를 내 문서에 적용한다.

둘 중 하나만 이기고, 진 쪽은 이긴 쪽의 상태를 쓴다. 락도, 리더도 없다.

## Redis 는 전달만 한다

프로세스가 여럿이어도 갈라지지 않는 이유는 CRDT 이기 때문이다 — 같은 업데이트
집합을 받으면 순서와 무관하게 같은 결과가 된다. 그래서 리더가 필요 없고,
Redis 는 업데이트를 다른 프로세스로 **던지는** 역할만 한다. Redis 가 비어도
잃는 것이 없다: durable 은 `page_collab` 이다.

## 스냅샷은 판을 만들지 않는다

`page_version` 에 저장하면 이력이 오염된다 — `page_draft` 의 주석에 적힌 것과
같은 이유다. 스냅샷은 공유 초안일 뿐이고, **판을 만드는 것은 사람이 게시할
때뿐이다.**
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pycrdt import (
    Awareness,
    Doc,
    Text,
    YMessageType,
    create_awareness_message,
    create_sync_message,
    handle_sync_message,
    read_message,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.logging import get_logger
from ieum.modules.wiki.models import Page, PageCollab, PageVersion

logger = get_logger(__name__)

#: Y.Doc 안에서 본문을 담는 이름. 클라이언트도 같은 이름을 쓴다.
BODY_KEY = "body"

#: 바뀐 것이 있으면 이만큼마다 한 번 스냅샷을 남긴다.
#:
#: "조용해지면 저장" 이 아니다 — 계속 타이핑하는 문서는 조용해질 틈이 없어서
#: 영원히 저장되지 않는다. 짧으면 쓰기가 잦고, 길면 프로세스가 죽을 때 잃는
#: 양이 늘어난다. 잃는 것은 **공유 초안의 몇 초**이고 게시된 판이 아니다.
SNAPSHOT_SECONDS = 3.0

#: 한 문서에 붙을 수 있는 편집자 수. 넘으면 거절한다 — 방 하나가 프로세스의
#: 메모리와 팬아웃을 통째로 먹는 것을 막는다.
MAX_CLIENTS = 30


def channel_for(page_id: UUID) -> str:
    return f"ieum:collab:{page_id}"


def text_of(state: bytes) -> str:
    """CRDT 상태에서 본문을 뽑는다. **반대 방향은 없다** — 텍스트로는 상태를
    복원할 수 없고, 복원한 척하면 편집 이력이 사라진다."""
    doc: Doc[Text] = Doc()
    doc[BODY_KEY] = Text()
    doc.apply_update(state)
    return str(doc[BODY_KEY])


def seed_state(body: str) -> bytes:
    """본문 하나를 담은 새 상태. **씨 뿌리기에만 쓴다** (모듈 주석 참조)."""
    doc: Doc[Text] = Doc()
    doc[BODY_KEY] = Text()
    if body:
        doc[BODY_KEY] += body
    return doc.get_update()


async def load_or_seed(session: AsyncSession, page_id: UUID) -> bytes:
    """이 문서의 CRDT 상태. 없으면 게시된 본문으로 만들어 심는다.

    **"있으면 읽고 없으면 심는다" 로 쓰지 않는다.** 그렇게 쓰면 두 프로세스가
    동시에 "없다" 를 보고 각자 심는 창이 열리고, 텍스트 CRDT 는 두 삽입을
    충실히 병합해서 본문이 **두 번** 들어간 문서를 만든다.

    그래서 갈래를 없앤다. 언제나 같은 세 걸음을 걷는다:

    1. 게시된 본문으로 상태를 만들어 `ON CONFLICT DO NOTHING` 으로 넣는다.
    2. **행을 읽는다.** 이미 있었으면 1번이 아무 일도 안 했으므로 그 행이고,
       경쟁에서 졌으면 이긴 쪽의 행이다.
    3. 읽은 것을 쓴다. 내가 만든 상태는 **버린다.**

    3번이 요점이다. 내가 만든 것을 쓰면 진 쪽과 이긴 쪽이 서로 다른 문서에서
    출발하고, 클라이언트들이 만나는 순간 본문이 겹쳐 보인다. 값은 조금 낭비다
    (안 쓸 상태를 만든다) — 갈래 하나를 없애는 값으로 싸다.
    """
    await session.execute(
        pg_insert(PageCollab)
        .values(page_id=page_id, state=seed_state(await _published_body(session, page_id)))
        .on_conflict_do_nothing(index_elements=[PageCollab.page_id])
    )
    await session.commit()

    row = (
        await session.execute(select(PageCollab).where(PageCollab.page_id == page_id))
    ).scalar_one_or_none()
    if row is None:  # pragma: no cover - 방금 넣었는데 없으면 DB 가 이상하다
        raise RuntimeError("page_collab 을 심었는데 다시 읽히지 않는다")
    return row.state


async def _published_body(session: AsyncSession, page_id: UUID) -> str:
    page = await session.get(Page, page_id)
    if page is None or page.current_version_id is None:
        return ""
    version = await session.get(PageVersion, page.current_version_id)
    return "" if version is None else version.body


async def save_snapshot(
    session: AsyncSession, page_id: UUID, state: bytes, *, saved_by: UUID | None
) -> None:
    """상태를 덮어쓴다. 판을 만들지 않는다 — 게시는 사람이 한다."""
    await session.execute(
        pg_insert(PageCollab)
        .values(page_id=page_id, state=state, saved_by=saved_by)
        .on_conflict_do_update(
            index_elements=[PageCollab.page_id],
            set_={"state": state, "saved_by": saved_by},
        )
    )
    await session.commit()


@dataclass(eq=False)
class Client:
    """방에 붙은 한 사람의 연결.

    보내기는 **큐를 거친다.** 소켓 하나가 느리면 그 사람 때문에 다른 사람의
    편집이 밀리는데, 편집은 밀리면 안 되는 종류다.
    """

    user_id: UUID
    send: Callable[[bytes], Awaitable[None]]
    outbox: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=256))

    def offer(self, message: bytes) -> bool:
        """넣는다. 큐가 찼으면 **버리고 그렇다고 말한다** — 조용히 버리면 그
        사람의 화면만 조용히 뒤처진다."""
        try:
            self.outbox.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True

    async def pump(self) -> None:
        while True:
            await self.send(await self.outbox.get())


class Room:
    """문서 하나의 편집 방. 프로세스마다 하나씩 있고, Redis 로 서로 이어진다."""

    def __init__(self, page_id: UUID, state: bytes, redis: Redis) -> None:
        self.page_id = page_id
        self._redis = redis
        self._doc: Doc[Text] = Doc()
        self._doc[BODY_KEY] = Text()
        self._doc.apply_update(state)
        self._awareness = Awareness(self._doc)
        self._clients: set[Client] = set()
        #: 원격에서 온 업데이트를 적용하는 중인가. Redis 로 되돌려 보내면
        #: 두 프로세스가 서로에게 같은 것을 영원히 던진다.
        self._applying_remote = False
        self._dirty = False
        self._last_saved = asyncio.get_running_loop().time()
        self._subscription = self._doc.observe(self._on_update)
        self._pump: asyncio.Task[None] | None = None
        self._saver: asyncio.Task[None] | None = None
        self._save: Callable[[bytes, UUID | None], Awaitable[None]] | None = None
        self._last_editor: UUID | None = None

    # ── 생명주기 ────────────────────────────────────────────────

    async def start(self, save: Callable[[bytes, UUID | None], Awaitable[None]]) -> None:
        self._save = save
        self._pump = asyncio.create_task(self._listen())
        self._saver = asyncio.create_task(self._save_loop())

    async def stop(self) -> None:
        """마지막 사람이 나가면 닫는다. **닫기 전에 한 번 저장한다** — 안 하면
        마지막 몇 초의 편집이 사라진다."""
        for task in (self._pump, self._saver):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._dirty:
            await self._flush()
        self._doc.unobserve(self._subscription)

    # ── 클라이언트 ──────────────────────────────────────────────

    @property
    def clients(self) -> int:
        return len(self._clients)

    def full(self) -> bool:
        return len(self._clients) >= MAX_CLIENTS

    async def join(self, client: Client) -> None:
        """붙자마자 **문서와 프레즌스를 둘 다** 보낸다.

        프레즌스를 안 보내면 새로 들어온 사람에게는 아무도 없는 것으로 보이고,
        먼저 있던 사람들은 그 사실을 모른다 — 각자 자기 상태만 방송하므로
        누군가 다음 키를 누를 때까지 빈 방이다.
        """
        self._clients.add(client)
        client.offer(create_sync_message(self._doc))
        known = list(self._awareness.states.keys())
        if known:
            client.offer(create_awareness_message(self._awareness.encode_awareness_update(known)))

    def leave(self, client: Client) -> None:
        self._clients.discard(client)

    async def handle(self, client: Client, message: bytes) -> None:
        """클라이언트가 보낸 한 통. 동기화면 문서에, 프레즌스면 그대로 중계."""
        if not message:
            return
        kind = message[0]
        if kind == YMessageType.SYNC:
            reply = handle_sync_message(message[1:], self._doc)
            if reply is not None:
                client.offer(reply)
            self._last_editor = client.user_id
        elif kind == YMessageType.AWARENESS:
            update = read_message(message[1:])
            self._awareness.apply_awareness_update(update, origin=self)
            self._relay(create_awareness_message(update), skip=client)
            await self._publish(message)

    # ── 안쪽 ────────────────────────────────────────────────────

    def _on_update(self, event: Any) -> None:
        """문서가 바뀌었다. 붙어 있는 사람들과 다른 프로세스에 알린다."""
        self._dirty = True
        message = _sync_update(event.update)
        self._relay(message, skip=None)
        if not self._applying_remote:
            # 원격에서 받은 것을 되돌려 보내지 않는다 (무한 왕복).
            asyncio.ensure_future(self._publish(message))  # noqa: RUF006

    def _relay(self, message: bytes, *, skip: Client | None) -> None:
        for client in self._clients:
            if client is skip:
                continue
            if not client.offer(message):
                logger.warning(
                    "collab.outbox_full", page_id=str(self.page_id), user_id=str(client.user_id)
                )

    async def _publish(self, message: bytes) -> None:
        """다른 프로세스로 던진다. 못 던져도 **이 프로세스의 편집은 돈다.**

        그 성질이 여기서 나오는 것은 아니다. `_on_update` 가 동기 콜백이라
        기다릴 수 없어 fire-and-forget 으로 띄우고, 그래서 실패가 편집 경로로
        올라오지 않는다 — 삼키는 것을 빼도 편집은 그대로 돈다(실제로 되돌려
        확인했고, 시험이 붉어지지 않았다).

        그래도 삼키는 이유는 다르다: 안 삼키면 Redis 가 없는 동안 파이썬이
        타스크마다 "Task exception was never retrieved" 를 찍어 로그를
        메운다. 그러면 정작 봐야 할 줄이 묻힌다.

        잃는 것은 다른 프로세스와의 동기화이고, 그건 다음 업데이트에 따라온다
        (CRDT 는 순서를 안 따진다).
        """
        with contextlib.suppress(Exception):
            await self._redis.publish(channel_for(self.page_id), message)

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel_for(self.page_id))
        try:
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                self._apply_remote(bytes(raw["data"]))
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    def _apply_remote(self, message: bytes) -> None:
        if not message:
            return
        kind = message[0]
        self._applying_remote = True
        try:
            if kind == YMessageType.SYNC:
                handle_sync_message(message[1:], self._doc)
            elif kind == YMessageType.AWARENESS:
                update = read_message(message[1:])
                self._awareness.apply_awareness_update(update, origin=self)
                self._relay(create_awareness_message(update), skip=None)
        finally:
            self._applying_remote = False

    async def _save_loop(self) -> None:
        while True:
            await asyncio.sleep(SNAPSHOT_SECONDS)
            now = asyncio.get_running_loop().time()
            if not self._dirty:
                continue
            if now - self._last_saved >= SNAPSHOT_SECONDS:
                await self._flush()

    async def _flush(self) -> None:
        if self._save is None:  # pragma: no cover - start() 전에 부를 일이 없다
            return
        self._dirty = False
        self._last_saved = asyncio.get_running_loop().time()
        await self._save(self._doc.get_update(), self._last_editor)

    def body(self) -> str:
        """지금 방의 본문. 시험과 게시가 본다."""
        return str(self._doc[BODY_KEY])


def _sync_update(update: bytes) -> bytes:
    """SYNC_UPDATE 한 통으로 감싼다.

    `create_update_message` 가 하는 일과 같지만, 그 함수는 `pycrdt` 의 클라이언트
    쪽 헬퍼라 이름이 뜻을 흐린다 — 여기서 만드는 것은 서버가 방송하는 통이다.
    """
    from pycrdt import create_update_message

    return create_update_message(update)


__all__ = [
    "BODY_KEY",
    "MAX_CLIENTS",
    "SNAPSHOT_SECONDS",
    "Client",
    "Room",
    "channel_for",
    "load_or_seed",
    "save_snapshot",
    "seed_state",
    "text_of",
]
