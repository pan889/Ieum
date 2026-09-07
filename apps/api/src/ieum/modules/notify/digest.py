"""다이제스트 — 하루치 알림을 한 통으로.

알림마다 메일이 한 통씩 가면 아무도 안 읽고, 결국 메일을 통째로 끈다. 끈
사람에게는 아무것도 못 알린다. 그래서 "묶어서 하루 한 번" 이 필요하다.

**받는 사람의 아침에 보낸다.** 새벽 세 시에 온 요약은 안 읽는다. 사용자마다
시간대가 있으므로(`user.timezone`), 매시 돌면서 그 사람의 지금이 아침인지만
본다 — 전 세계 한 시각에 몰아 보내면 절반에게는 한밤중이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.i18n import translator_for
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.identity import contracts as identity
from ieum.modules.notify.mail import Mail
from ieum.modules.notify.models import NotificationPreference
from ieum.modules.notify.repository import NotificationRepository, PreferenceRepository

log = get_logger(__name__)

#: 받는 사람의 지역 시각으로 이 시간대에 보낸다.
DIGEST_HOUR = 8
#: 한 통에 담을 최대 줄 수. 넘치면 "그리고 N개 더" 로 줄인다.
MAX_LINES = 20
#: 이만큼 안 지났으면 이미 보낸 것으로 본다. 시계가 조금 밀려도 두 번 안 간다.
MIN_GAP = timedelta(hours=20)


@dataclass(frozen=True, slots=True)
class Digest:
    """보낼 한 통과, 함께 갱신할 설정 행."""

    mail: Mail
    preference: NotificationPreference
    covered: int


def is_morning(now: datetime, timezone: str) -> bool:
    """받는 사람의 지금이 다이제스트 시간대인가.

    모르는 시간대는 UTC 로 본다. 시간대 이름이 틀렸다고 알림을 아예 안 보내는
    것보다, 조금 엉뚱한 시각에라도 보내는 편이 낫다.
    """
    try:
        zone = ZoneInfo(timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now.astimezone(zone).hour == DIGEST_HOUR


async def collect(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None
) -> list[Digest]:
    """지금 보내야 할 다이제스트를 모은다. 보내지는 않는다.

    발송은 트랜잭션 밖에서 한다 — SMTP 가 느리면 커넥션을 붙잡고 락이 쌓인다
    (worker/tasks.py 파이프라인 주석).
    """
    moment = now or utcnow()
    preferences = PreferenceRepository(session)
    notifications = NotificationRepository(session)

    out: list[Digest] = []
    for preference in await preferences.daily_digest_users():
        if preference.last_digest_at is not None and moment - preference.last_digest_at < MIN_GAP:
            continue
        user = await identity.get_user(session, preference.user_id)
        if user is None or not user.is_active:
            continue
        if not is_morning(moment, user.timezone):
            continue

        rows = await notifications.since(
            preference.user_id, preference.last_digest_at, MAX_LINES + 1
        )
        if not rows:
            # 보낼 것이 없으면 시각도 안 건드린다. 다음 시간에 다시 본다.
            continue

        translate = translator_for(settings.i18n_catalog_dir, user.locale)
        shown = rows[:MAX_LINES]
        lines = [f"- {row.title}" for row in shown]
        if len(rows) > MAX_LINES:
            lines.append(translate("notifications:digest.more", count=len(rows) - MAX_LINES))
        body = "\n".join([translate("notifications:digest.intro", count=len(rows)), "", *lines])

        out.append(
            Digest(
                mail=Mail(
                    to=user.email,
                    subject=translate("notifications:digest.subject", count=len(rows)),
                    body=body,
                    link="/notifications",
                ),
                preference=preference,
                covered=len(rows),
            )
        )
    return out


def mark_sent(digests: list[Digest], *, now: datetime | None = None) -> None:
    """보낸 것으로 표시한다. 발송이 끝난 뒤 호출한다."""
    moment = now or utcnow()
    for digest in digests:
        digest.preference.last_digest_at = moment


__all__ = ["DIGEST_HOUR", "Digest", "collect", "is_morning", "mark_sent"]
