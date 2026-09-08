"""동시 편집 라우터 — 표 하나와 소켓 하나 (B16, M5).

## 왜 소켓이 자기 인증을 따로 하나

이 설치의 인증은 **Authorization 헤더**다(`core/deps.py`). 브라우저의
`WebSocket` 은 핸드셰이크에 헤더를 못 붙인다 — 그래서 소켓은 다른 길로
들어와야 한다. 흔한 세 가지 중 고른 것과 버린 이유:

- `?token=<액세스 토큰>`: **버렸다.** 액세스 토큰이 URL 에 실리면 프록시
  로그·리퍼러·브라우저 이력에 남는다. 유효 기간이 남은 토큰이 로그에 있는
  것은 그 자체로 사고다.
- `Sec-WebSocket-Protocol` 에 토큰: 헤더라 로그에는 덜 남지만, 그 헤더는
  프로토콜 협상용이고 값이 그대로 응답에 되돌아온다.
- **표(ticket) 한 장**: 인증된 REST 경로에서 짧게 사는 한 번 쓰는 표를 받아
  URL 에 싣는다. 로그에 남아도 이미 쓰인 표이고 30초면 죽는다.

권한 검사가 REST 쪽에 있는 것도 이 선택의 이득이다: `wiki.page.edit` 과 문서
제한을 이미 있는 서비스가 보고, 소켓은 "표가 맞나" 만 본다.

## 표는 한 번만 쓴다

`GETDEL` 로 꺼낸다. 두 번 쓸 수 있으면 URL 이 새는 순간 남의 편집 세션에
붙을 수 있고, 그건 문서를 고칠 수 있다는 뜻이다.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url

from ieum.config import Settings, get_settings
from ieum.core.deps import AppSettings, CurrentActor, DbSession, PermissionDep
from ieum.core.logging import get_logger
from ieum.modules.wiki.collab import MAX_CLIENTS, Client
from ieum.modules.wiki.rooms import registry
from ieum.modules.wiki.service import PageService

collab_router = APIRouter(prefix="/pages", tags=["wiki"])

logger = get_logger(__name__)

#: 표의 수명. 화면이 표를 받아 바로 소켓을 여는 데 걸리는 시간보다 넉넉하고,
#: 로그에 남은 표가 쓸모 있을 시간보다는 짧아야 한다.
TICKET_TTL_SECONDS = 30


def _ticket_key(ticket: str) -> str:
    return f"ieum:collab:ticket:{ticket}"


class CollabTicketResponse(BaseModel):
    #: 소켓 URL 에 실을 표. **한 번만 쓸 수 있다.**
    ticket: str
    #: 붙을 곳. 화면이 경로를 손으로 짜지 않게 서버가 준다.
    url: str
    #: 지금 이 문서를 보고 있는 사람 수(이 프로세스 기준). 화면이 "혼자인가"
    #: 를 붙기 전에 알려 줄 수 있게 준다 — 정확한 수는 붙은 뒤 프레즌스가 준다.
    editors: int
    #: 한 문서에 붙을 수 있는 상한. 거절당한 이유를 화면이 말할 수 있게.
    max_editors: int


@collab_router.post("/{page_id}/collab-ticket", response_model=CollabTicketResponse)
async def mint_collab_ticket(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    settings: AppSettings,
    page_id: UUID,
) -> CollabTicketResponse:
    """편집 세션에 붙을 표를 한 장 낸다.

    **권한은 여기서 본다.** 소켓 쪽에서 보면 세션·권한·문서 제한을 소켓 코드가
    다시 구현하게 되고, 두 벌은 어긋난다.
    """
    service = PageService(session, permissions)
    page = await service.page_for_edit(actor, page_id)

    ticket = secrets.token_urlsafe(32)
    client: Redis = redis_from_url(settings.redis_url)  # type: ignore[no-untyped-call]
    try:
        await client.set(_ticket_key(ticket), f"{page.id}:{actor.user_id}", ex=TICKET_TTL_SECONDS)
    finally:
        await client.aclose()

    return CollabTicketResponse(
        ticket=ticket,
        url=f"/api/v1/pages/{page.id}/collab?ticket={ticket}",
        editors=registry.editors(page.id),
        max_editors=MAX_CLIENTS,
    )


@collab_router.websocket("/{page_id}/collab")
async def collab_socket(
    socket: WebSocket,
    page_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    ticket: str = Query(min_length=8, max_length=128),
) -> None:
    """Yjs 동기화 프로토콜을 그대로 나른다.

    서버는 통의 **뜻을 거의 모른다**: 동기화는 `pycrdt` 가 문서에 적용하고,
    프레즌스는 그대로 중계한다. 안을 파싱하면 클라이언트가 커서에 무엇을 담을지
    서버에 물어야 하고, 색깔 하나 바꾸는 데 배포가 필요해진다.
    """
    user_id = await admit(settings.redis_url, ticket, page_id)
    if user_id is None:
        # 열어 주지 않는다. `accept()` 전의 close 는 핸드셰이크를 거절한다.
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    room = await registry.acquire(page_id, settings.redis_url)
    if room.full():
        await registry.release(page_id)
        await socket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    await socket.accept()
    client = Client(user_id=user_id, send=socket.send_bytes)
    pump = None
    try:
        pump = asyncio.create_task(client.pump())
        await room.join(client)
        while True:
            await room.handle(client, await socket.receive_bytes())
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("collab.socket_failed", page_id=str(page_id), user_id=str(user_id))
    finally:
        if pump is not None:
            pump.cancel()
        room.leave(client)
        await registry.release(page_id)


async def admit(redis_url: str, ticket: str, page_id: UUID) -> UUID | None:
    """표를 받고 **이 문서의 것인지** 본다. 통과하면 그 사람의 id.

    소켓 핸들러에서 인라인으로 두지 않는 이유: 이 두 줄이 곧 "누가 이 문서를
    고칠 수 있나" 이고, 소켓 안에 있으면 시험이 브라우저를 띄워야만 닿는다.
    이름을 붙여 밖으로 내면 문서 A 의 표로 B 를 열려는 시도를 직접 볼 수 있다.
    """
    holder = await _redeem(redis_url, ticket)
    if holder is None or holder[0] != page_id:
        return None
    return holder[1]


async def _redeem(redis_url: str, ticket: str) -> tuple[UUID, UUID] | None:
    """표를 **꺼낸다**(읽고 지운다). 두 번 쓸 수 없어야 한다."""
    client: Redis = redis_from_url(redis_url)  # type: ignore[no-untyped-call]
    try:
        raw = await client.getdel(_ticket_key(ticket))
    finally:
        await client.aclose()
    if raw is None:
        return None
    try:
        page_part, user_part = bytes(raw).decode().split(":", 1)
        return UUID(page_part), UUID(user_part)
    except ValueError:
        return None


__all__ = ["TICKET_TTL_SECONDS", "admit", "collab_router"]
