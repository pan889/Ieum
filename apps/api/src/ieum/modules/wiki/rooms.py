"""편집 방 레지스트리 — 프로세스마다 하나 (B16, M5).

방은 **붙은 사람이 있는 동안만** 산다. 문서마다 하나씩 영원히 들고 있으면
한 번 열린 문서의 CRDT 상태가 프로세스 메모리에 계속 쌓인다.

세는 것을 리스트가 아니라 **숫자로** 들고 있는 이유: 같은 문서에 붙는 소켓은
여러 개이고, 마지막 하나가 나갈 때 닫아야 한다. "누가 붙어 있나" 는 방이
알고, 여기는 "몇 개가 이 방을 잡고 있나" 만 안다.

방을 만들고 지우는 사이에 남이 끼어들면 방이 둘 생긴다 — 그러면 같은
프로세스 안에서 두 방이 갈라진다(Redis 를 거치므로 결국 만나지만, 그 사이의
프레즌스는 서로를 못 본다). 그래서 잠금 하나로 감싼다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.core.logging import get_logger
from ieum.db.session import get_session_factory
from ieum.modules.wiki.collab import Merge, Room, load_or_seed, save_snapshot

#: 세션을 어디서 얻는가. 기본값은 앱이 세운 전역 팩토리다.
#:
#: 넘길 수 있게 둔 이유: 방은 **요청 밖에서** 돌기 때문에 요청 스코프의 세션을
#: 쓸 수 없고, 그래서 전역을 본다. 그런데 전역을 코드 안에서 직접 부르면
#: 레지스트리를 시험할 수 없다 — 시험 앱은 `init_engine()` 대신 의존성을
#: 갈아끼우므로 그 전역이 비어 있다. 시험할 수 없는 자리는 결국 안 시험한
#: 자리가 된다.
SessionSource = Callable[[], async_sessionmaker[AsyncSession]]

logger = get_logger(__name__)


class RoomRegistry:
    def __init__(self, *, sessions: SessionSource = get_session_factory) -> None:
        self._rooms: dict[UUID, Room] = {}
        self._holders: dict[UUID, int] = {}
        #: 닫히는 중인 방의 닫기 태스크. **다음 `acquire` 가 이것을 기다린다.**
        #:
        #: 닫기는 마지막 저장을 포함한다. 기다리지 않으면 새로고침이 저장 전의
        #: 상태를 읽고 방금 친 글이 없는 문서가 뜬다 — 오류 하나 없이. 새로고침은
        #: "닫고 곧바로 연다" 라서 이 창에 정확히 들어간다.
        self._closing: dict[UUID, asyncio.Task[None]] = {}
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()
        self._sessions = sessions

    def editors(self, page_id: UUID) -> int:
        """이 프로세스에서 이 문서를 보고 있는 사람 수.

        **설치 전체의 수가 아니다.** 정확한 수는 붙은 뒤 프레즌스가 알려 준다 —
        여기서 전체를 세려면 Redis 에 카운터를 두고 프로세스가 죽을 때 그것을
        되돌려야 하는데, 되돌리지 못한 카운터는 영원히 "3명이 편집 중" 이라고
        말한다. 틀린 숫자를 계속 보여 주는 쪽이 낮게 보여 주는 쪽보다 나쁘다.
        """
        room = self._rooms.get(page_id)
        return 0 if room is None else room.clients

    async def acquire(self, page_id: UUID, redis_url: str) -> Room:
        async with self._lock:
            # 이 문서가 닫히는 중이면 **저장이 끝나기를 기다린다.** 안 기다리면
            # 저장 전의 상태를 심고, 닫히던 방이 들고 있던 글이 사라진다.
            # 꺼내지 않고 **본다.** 기다리는 중에 이 붙기가 취소되면(소켓이
            # 핸드셰이크 도중에 끊기면) 꺼내 둔 것은 아무도 안 기다리게 되고,
            # 그 다음 붙기가 저장 전의 상태를 심는다. 치우는 것은 닫기가
            # 끝날 때 콜백이 한다.
            closing = self._closing.get(page_id)
            if closing is not None:
                # `shield` 로 감싼다: 이 붙기가 취소돼도 **닫기는 끝까지 간다.**
                # 중간에 끊긴 닫기는 마지막 저장을 안 남긴다.
                await asyncio.shield(closing)

            self._holders[page_id] = self._holders.get(page_id, 0) + 1
            room = self._rooms.get(page_id)
            if room is not None:
                return room

            async with self._sessions()() as session:
                state = await load_or_seed(session, page_id)

            room = Room(page_id, state, self._client(redis_url))
            await room.start(_saver(page_id, self._sessions))
            self._rooms[page_id] = room
            return room

    async def release(self, page_id: UUID) -> None:
        async with self._lock:
            left = self._holders.get(page_id, 0) - 1
            if left > 0:
                self._holders[page_id] = left
                return
            self._holders.pop(page_id, None)
            room = self._rooms.pop(page_id, None)
            if room is None:
                return
            # 닫기는 마지막 저장을 기다리므로 여기서 기다리지 않는다 — 소켓이
            # 끊기는 자리라 붙잡아 둘 이유가 없다. 대신 **그 태스크를
            # 기억한다**: 다음 `acquire` 가 그것을 기다려야 저장 전의 상태를
            # 심지 않는다.
            task = asyncio.create_task(room.stop())
            self._closing[page_id] = task
            task.add_done_callback(lambda done: self._forget(page_id, done))

    def _forget(self, page_id: UUID, task: asyncio.Task[None]) -> None:
        # 그 사이 새 닫기가 등록됐으면 그것을 지우지 않는다.
        if self._closing.get(page_id) is task:
            del self._closing[page_id]

    def _client(self, redis_url: str) -> Redis:
        if self._redis is None:
            self._redis = redis_from_url(redis_url)  # type: ignore[no-untyped-call]
        return self._redis

    async def aclose(self) -> None:
        """앱이 내려갈 때. 방을 다 닫고 **저장까지 기다린다.**

        `release` 는 닫기를 태스크로 띄우므로 여기서 그 태스크들을 모아
        기다린다. 안 기다리면 프로세스가 내려가면서 마지막 몇 초의 편집이
        사라진다.
        """
        for page_id in list(self._rooms):
            self._holders[page_id] = 1
            await self.release(page_id)
        pending = list(self._closing.values())
        if pending:
            # 하나가 터져도 남은 것은 닫는다 — 그래서 예외를 모은다. 그런데
            # **삼키지는 않는다**: 여기서 실패한 저장은 마지막 기회였고, 그
            # 편집은 이제 아무 데도 없다. 아무 줄도 남기지 않으면 사람은
            # 나중에 "내가 쓴 게 없다" 로만 그 사실을 만난다.
            for outcome in await asyncio.gather(*pending, return_exceptions=True):
                if isinstance(outcome, BaseException):
                    logger.error("collab.shutdown_save_failed", error=str(outcome))
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


def _saver(
    page_id: UUID, sessions: SessionSource
) -> Callable[[Merge, UUID | None], Awaitable[None]]:
    """방이 부를 저장 함수. 요청 밖에서 도므로 세션을 새로 뜬다.

    상태 대신 **합치는 함수**를 받는다: 스냅샷은 읽기-합치기-쓰기이고, 읽은
    것을 문서에 합치는 일은 방만 할 수 있다 (`collab.Merge` 주석 참조).
    """

    async def save(merge: Merge, saved_by: UUID | None) -> None:
        async with sessions()() as session:
            await save_snapshot(session, page_id, merge, saved_by=saved_by)

    return save


#: 프로세스에 하나. 방은 프로세스 안의 것이고, 프로세스 사이는 Redis 가 잇는다.
registry = RoomRegistry()

__all__ = ["RoomRegistry", "registry"]
