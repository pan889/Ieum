"""만족도 조사를 보낸다 (feature-map C11).

`csat.py` 가 토큰과 값을 다루고, 여기가 **행을 쓴다.**

## 왜 워커에서 도는가

`issue.transitioned` 를 아웃박스에서 받는다. 요청 경로에서 보내면 상담원의
"해결" 버튼이 SMTP 를 기다리고, 메일 서버가 죽은 날에는 티켓을 닫을 수 없다 —
조사는 이미 일어난 일에 대한 후속이지 그 일의 일부가 아니다.

## 보내지 않는 경우

- **이미 보냈다.** `csat_sent_at` 이 근거다. 점수가 비었다는 것을 근거로
  삼으면, 답하지 않은 고객은 티켓이 다시 열렸다 닫힐 때마다 또 받는다.
- **반송된 주소.** 회신과 같은 판단이다(C6): 검증되지 않은 게스트 주소로 계속
  보내면 남의 주소를 적어 넣은 경우 그 사람에게 계속 배달을 시도한다.
- **상담원이 만든 티켓.** 요청한 사람이 없다 — 물을 상대가 없다.
- **자동화가 닫은 것도 보낸다.** `automated` 표시를 안 본다: 고리가 없기
  때문이다(조사 응답은 이벤트를 내지 않는다). 규칙이 닫은 티켓이라고 고객이
  덜 궁금한 것은 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.i18n import translator_for
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.desk.csat import encode_survey_token
from ieum.modules.desk.models import TicketExt
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues
from ieum.modules.notify.contracts import Mail

log = get_logger(__name__)


@dataclass(slots=True)
class SurveyContext:
    """워커가 이벤트마다 넘기는 것."""

    session: AsyncSession
    settings: Settings


async def collect_survey_mail(ctx: SurveyContext, envelope: EventEnvelope) -> list[Mail]:
    """이 전이로 조사를 보내야 하면 그 한 통을. 아니면 빈 목록.

    `collect_reply_mail` 과 같은 모양이다 — 워커가 트랜잭션 안에서 모으고
    **닫은 뒤에** 보낸다.
    """
    if envelope.event_type != "issue.transitioned":
        return []
    if str(envelope.payload.get("to_state_category") or "") != "done":
        return []

    session = ctx.session
    issue_id = envelope.aggregate_id
    ticket = await session.get(TicketExt, issue_id)
    if ticket is None:
        return []
    # **이미 보냈으면 안 보낸다.** 이 한 줄이 "한 번만" 을 지킨다.
    if ticket.csat_sent_at is not None:
        return []
    if ticket.email_bounced_at is not None:
        log.info("desk.csat.suppressed", issue_id=str(issue_id), reason="bounced")
        return []

    to, locale = await _requester(session, ticket)
    if not to:
        # 상담원이 대신 넣은 티켓에는 물을 상대가 없다. 조용히 넘긴다 —
        # 오류가 아니라 답이다.
        return []

    ref = await issues.get_issue(session, issue_id)
    if ref is None:
        return []
    key = str(envelope.payload.get("issue_key") or "")

    # **표시를 먼저 남긴다.** 발송은 트랜잭션 밖이라 성공을 기다릴 수 없고,
    # 실패했다고 다시 보내면 메일 서버가 잠깐 흔들린 날 고객이 같은 조사를
    # 여러 통 받는다 — 안 온 것보다 나쁘다.
    ticket.csat_sent_at = utcnow()
    await session.flush()

    token = encode_survey_token(issue_id, ctx.settings)
    # 고객의 언어로 쓴다. 티켓을 닫은 상담원의 언어가 아니다 (i18n.md 3절).
    translate = translator_for(ctx.settings.i18n_catalog_dir, locale or None)
    log.info("desk.csat.queued", issue_id=str(issue_id))
    return [
        Mail(
            to=to,
            subject=translate("desk:csat.mailSubject", key=key, summary=ref.summary),
            body=translate("desk:csat.mailBody", summary=ref.summary),
            link=f"/survey?token={token}",
        )
    ]


# ── 내부 ────────────────────────────────────────────────────────


async def _requester(session: AsyncSession, ticket: TicketExt) -> tuple[str, str]:
    """요청한 사람의 주소와 언어.

    게스트에게는 언어가 없다 — 설치의 기본으로 보낸다. 그 편이 "영어로 보내고
    본다" 보다 낫다: 대부분의 게스트는 그 조직의 고객이다.
    """
    if ticket.reporter_customer_id is not None:
        ref = await identity.get_user(session, ticket.reporter_customer_id)
        if ref is not None:
            return ref.email, ref.locale
    if ticket.guest_email:
        return ticket.guest_email, ""
    return "", ""
