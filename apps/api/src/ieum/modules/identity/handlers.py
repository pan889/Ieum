"""identity 이벤트 구독.

초대 토큰은 **여기서 만든다**. 이벤트 페이로드에 실으면 아웃박스에도, 웹훅
전송 본문에도 남는다 — 계정을 활성화할 수 있는 자격 증명이 로그를 볼 수 있는
모든 곳에 흩어진다.

메일을 여기서 보내지 않는다. 만들어 돌려주고 워커가 트랜잭션 밖에서 보낸다
(worker/tasks.py 의 파이프라인 주석). SMTP 가 느리면 DB 커넥션을 붙잡고
락이 쌓인다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.i18n import translator_for
from ieum.core.logging import get_logger
from ieum.modules.identity.invites import encode_invite_token
from ieum.modules.identity.repository import UserRepository
from ieum.modules.notify.contracts import Mail

log = get_logger(__name__)

INVITED_EVENT = "identity.user.invited"


@dataclass(slots=True)
class HandlerContext:
    session: AsyncSession
    settings: Settings


async def collect_invite_mail(ctx: HandlerContext, envelope: EventEnvelope) -> list[Mail]:
    """초대장 한 통. 보낼 것이 없으면 빈 목록.

    이 메일이 없으면 초대받은 사람은 계정을 활성화할 방법이 아예 없다 —
    토큰이 아무 데도 나가지 않기 때문이다.
    """
    if envelope.event_type != INVITED_EVENT:
        return []

    user = await UserRepository(ctx.session).get(envelope.aggregate_id)
    if user is None:
        return []
    # 이미 활성화된 사람에게 초대장을 다시 보내지 않는다. 아웃박스가 재시도될
    # 때 옛 초대가 되살아나면 "왜 또 왔지" 가 된다.
    if user.status != "invited":
        return []

    token = encode_invite_token(user.id, ctx.settings)
    # 초대받은 사람의 언어로 쓴다. 초대한 사람의 언어가 아니다 (i18n.md 3절).
    translate = translator_for(ctx.settings.i18n_catalog_dir, user.locale)
    log.info("identity.invite_mail", user=str(user.id))
    return [
        Mail(
            to=user.email,
            subject=translate("auth:invite.mailSubject"),
            body=translate("auth:invite.mailBody", name=user.display_name),
            # 토큰은 주소에 담긴다. 화면이 그걸 그대로 서버에 돌려준다.
            link=f"/invite?token={token}",
        )
    ]


__all__ = ["HandlerContext", "collect_invite_mail"]
