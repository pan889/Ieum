"""워커 작업.

파이프라인은 명시적이다:

    아웃박스 폴링(SKIP LOCKED)
      → 알림 행 생성 + 웹훅 전송 행 생성
      → published_at 기록 → 커밋
      → (트랜잭션 밖) 메일 발송, 웹훅 HTTP 전송

발송을 트랜잭션 안에서 하지 않는다. SMTP 나 느린 엔드포인트 하나가
DB 트랜잭션을 붙잡고 있으면 락이 쌓인다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import get_settings
from ieum.core.attachments import AttachmentService
from ieum.core.crypto import SecretBox
from ieum.core.heartbeat import beat
from ieum.core.logging import get_logger
from ieum.core.outbox import dispatch, fetch_unpublished, publish
from ieum.core.permissions import get_permission_service
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.db.session import session_scope
from ieum.modules.desk.clock import (
    ClockContext,
    handle_desk_event,
    sweep_breaches,
    sweep_escalations,
)
from ieum.modules.desk.email import EmailError, parse_message
from ieum.modules.desk.events import SlaBreached, SlaEscalated
from ieum.modules.desk.imap import ImapError, ImapSettings, fetch_unseen
from ieum.modules.desk.inbound import handle_inbound
from ieum.modules.desk.models import EmailChannel
from ieum.modules.desk.outbound import OutboundContext, collect_reply_mail
from ieum.modules.desk.rules import RuleContext, handle_automation
from ieum.modules.desk.survey import SurveyContext, collect_survey_mail
from ieum.modules.identity.handlers import HandlerContext as IdentityContext
from ieum.modules.identity.handlers import collect_invite_mail
from ieum.modules.issues import contracts as issue_contracts
from ieum.modules.issues.sprints import snapshot_sprints
from ieum.modules.notify import delivery as webhook_delivery
from ieum.modules.notify import digest as digests
from ieum.modules.notify.handlers import (
    HandlerContext,
    enqueue_webhooks,
    handle_issue_event,
    handle_page_event,
)
from ieum.modules.notify.mail import Mail, send_all
from ieum.modules.notify.models import Notification, Webhook
from ieum.modules.notify.repository import DeliveryRepository
from ieum.modules.notify.service import NotificationService
from ieum.modules.org import contracts as org_contracts

log = get_logger(__name__)

OUTBOX_BATCH = 100
WEBHOOK_BATCH = 50


async def drain_outbox() -> int:
    """미발행 이벤트를 처리한다. 처리한 건수를 돌려준다."""
    settings = get_settings()
    notification_ids: list[Any] = []
    # 알림 없이 나가는 메일. 초대장이 그렇다 — 받는 사람은 아직 계정을
    # 활성화하지 않아서 인앱 알림을 볼 수 없다.
    standalone: list[Mail] = []

    async with session_scope() as session:
        rows = await fetch_unpublished(session, limit=OUTBOX_BATCH)
        if not rows:
            return 0

        handler_ctx = HandlerContext(session=session, settings=settings)
        identity_ctx = IdentityContext(session=session, settings=settings)
        desk_ctx = OutboundContext(session=session, settings=settings)
        for row in rows:
            from ieum.core.events import EventEnvelope

            envelope = EventEnvelope(
                id=row.id,
                event_type=row.event_type,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                payload=dict(row.payload),
            )
            try:
                notification_ids.extend(await handle_issue_event(handler_ctx, envelope))
                notification_ids.extend(await handle_page_event(handler_ctx, envelope))
                standalone.extend(await collect_invite_mail(identity_ctx, envelope))
                # 메일 채널의 회신 (C6). 알림과 따로 나가는 이유: 받는 사람이
                # 우리 사용자가 아니라 **고객**이고, 인앱 알림을 볼 수 없다.
                standalone.extend(await collect_reply_mail(desk_ctx, envelope))
                # 만족도 조사 (C11). 회신과 같은 자리에서 모은다 — 받는 사람이
                # 우리 사용자가 아니라 고객이고, 인앱 알림을 볼 수 없다.
                standalone.extend(
                    await collect_survey_mail(
                        SurveyContext(session=session, settings=settings), envelope
                    )
                )
                await enqueue_webhooks(handler_ctx, envelope)
                # SLA 클럭 (C4). 여기서 도는 이유는 요청 경로에서 재면 아무도
                # 열어 보지 않은 티켓이 영원히 위반이 아니게 되기 때문이다.
                await handle_desk_event(ClockContext(session=session), envelope)
                # 자동화 규칙 (C9). **클럭 뒤에 둔다** — 규칙이 우선순위를
                # 올려도 이미 걸린 클럭의 목표는 그대로다(약속은 접수 시점에
                # 정해진다). 순서를 바꾸면 같은 티켓이 규칙의 실행 순간에
                # 따라 다른 목표를 갖는다.
                await handle_automation(RuleContext(session=session), envelope)
            # 한 건이 실패해도 배치 전체를 멈추지 않는다.
            except Exception as exc:
                row.attempts += 1
                row.last_error = f"{type(exc).__name__}: {exc}"
                log.error(
                    "outbox.handler_failed",
                    event_type=row.event_type,
                    id=str(row.id),
                    error=row.last_error,
                )
                continue

            # 모듈이 스스로 등록한 구독자도 돌린다.
            await dispatch(session, row)

        await session.flush()

    log.info("outbox.batch", processed=len(rows), notifications=len(notification_ids))

    if notification_ids:
        await send_pending_mail(notification_ids)
    if standalone:
        # 세션을 닫은 뒤 보낸다. SMTP 가 느려도 DB 커넥션을 붙잡지 않는다.
        log.info(
            "mail.standalone", queued=len(standalone), sent=await send_all(settings, standalone)
        )
    return len(rows)


async def send_pending_mail(notification_ids: list[Any]) -> int:
    """알림에 대응하는 메일을 보낸다. 트랜잭션 밖에서 돈다."""
    settings = get_settings()
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(Notification).where(Notification.id.in_(notification_ids))
                )
            )
            .scalars()
            .all()
        )
        service = NotificationService(session, settings)
        mails = await service.pending_mail(rows)

    if not mails:
        return 0
    # 세션을 닫은 뒤 보낸다. SMTP 가 느려도 DB 커넥션을 붙잡지 않는다.
    sent = await send_all(settings, mails)
    log.info("mail.batch", queued=len(mails), sent=sent)
    return sent


async def deliver_webhooks() -> int:
    """전송할 차례가 된 웹훅을 보낸다."""
    settings = get_settings()
    box = SecretBox(settings.secret_key.get_secret_value(), purpose="notify.webhook")

    async with session_scope() as session:
        deliveries = await DeliveryRepository(session).due(limit=WEBHOOK_BATCH)
        if not deliveries:
            return 0

        webhook_ids = {d.webhook_id for d in deliveries}
        webhooks = {
            w.id: w
            for w in (await session.execute(select(Webhook).where(Webhook.id.in_(webhook_ids))))
            .scalars()
            .all()
        }

        async with httpx.AsyncClient(follow_redirects=False) as client:
            for row in deliveries:
                webhook = webhooks.get(row.webhook_id)
                if webhook is None:
                    row.status = "abandoned"
                    row.error = "웹훅이 삭제됨"
                    continue
                if not webhook.enabled:
                    # 꺼진 웹훅은 재시도하지 않는다. 다시 켜면 새 이벤트부터 간다.
                    row.status = "abandoned"
                    row.error = "웹훅이 비활성 상태"
                    continue

                try:
                    secret = box.decrypt(webhook.secret_enc)
                # 시크릿을 못 푸는 웹훅 하나가 배치 전체를 멈추면 안 된다.
                # 키 로테이션이나 다른 키로 만든 DB 를 복원하면 실제로 생긴다.
                except Exception as exc:
                    row.status = "abandoned"
                    row.error = f"시크릿 복호화 실패: {type(exc).__name__}"
                    webhook.enabled = False
                    webhook.disabled_reason = "시크릿을 복호화할 수 없음"
                    log.error(
                        "webhook.secret_undecryptable",
                        webhook_id=str(webhook.id),
                        error=str(exc)[:200],
                    )
                    continue

                outcome = await webhook_delivery.post(webhook, row, secret, client=client)
                webhook_delivery.apply_outcome(webhook, row, outcome)
                log.info(
                    "webhook.delivered" if outcome.ok else "webhook.delivery_failed",
                    webhook_id=str(webhook.id),
                    delivery_id=str(row.id),
                    status=outcome.status_code,
                    attempts=row.attempts,
                )

    return len(deliveries)


async def sweep_attachments() -> int:
    """확정되지 않은 첨부를 치운다.

    업로드 중 창을 닫으면 pending 행과 (드물게) 반쯤 올라간 객체가 남는다.
    안 치우면 테이블에도 스토리지에도 계속 쌓인다.
    """
    settings = get_settings()
    store = ObjectStore(settings)
    async with session_scope() as session:
        removed = await AttachmentService(session, store).sweep_pending()
    return removed


async def sweep_sla() -> int:
    """SLA 목표를 넘긴 클럭을 찾아 알린다. 알린 건수를 돌려준다.

    **알림을 아웃박스로 낸다.** 여기서 직접 만들지 않는 이유는 알림 경로가
    하나여야 하기 때문이다 — 두 길로 만들면 환경설정(메일 끄기·워치)이 한쪽만
    적용된다.

    위반을 **스윕으로** 찾는 이유: 위반은 아무 일도 일어나지 않아서 생긴다.
    이벤트가 없으므로 이벤트로는 알 수 없고, 시간이 지났다는 사실을 누군가
    주기적으로 확인해야 한다.
    """
    # 블록 밖에서 세려고 미리 둔다. `with` 안에서만 대입하면, 그 안에서
    # 예외가 나면 아래 `len()` 이 이름을 못 찾아 원인이 바뀐다.
    count = 0
    async with session_scope() as session:
        breached = await sweep_breaches(session)
        count = len(breached)
        for clock in breached:
            ref = await issue_contracts.get_issue(session, clock.issue_id)
            if ref is None:
                continue
            project = await org_contracts.get_project(session, ref.project_id)
            publish(
                session,
                SlaBreached(
                    aggregate_id=clock.issue_id,
                    project_id=ref.project_id,
                    issue_key=f"{project.key}-{ref.key_seq}" if project else "",
                    summary=ref.summary,
                    policy_id=clock.policy_id,
                    assignee_id=ref.assignee_id,
                ),
            )
    return count


async def sweep_sla_escalations() -> int:
    """조건을 지난 에스컬레이션 규칙을 실행하고 알린다. 실행 건수를 돌려준다.

    `sweep_sla` 와 나누는 이유: 위반은 "약속을 놓쳤다" 이고 에스컬레이션은
    "그래서 이걸 했다" 다. 하나로 묶으면 75% 에서 미리 부르는 규칙을 표현할
    수 없다 — 그건 아직 위반이 아니다.
    """
    count = 0
    async with session_scope() as session:
        done = await sweep_escalations(session)
        count = len(done)
        for item in done:
            ref = await issue_contracts.get_issue(session, item.clock.issue_id)
            if ref is None:
                continue
            project = await org_contracts.get_project(session, ref.project_id)
            publish(
                session,
                SlaEscalated(
                    aggregate_id=item.clock.issue_id,
                    project_id=ref.project_id,
                    issue_key=f"{project.key}-{ref.key_seq}" if project else "",
                    summary=ref.summary,
                    policy_id=item.clock.policy_id,
                    rule=item.rule.key,
                    action=item.rule.action,
                    at_percent=item.at_percent,
                    to_user_id=item.rule.user_id,
                    priority=item.rule.priority,
                    assignee_id=ref.assignee_id,
                ),
            )
    return count


async def poll_email() -> int:
    """메일 채널을 훑어 받은 것을 티켓으로 만든다. 처리한 통 수를 돌려준다.

    **채널마다 세션을 새로 연다.** 한 채널이 죽어도 다른 채널이 멈추지 않아야
    하고, IMAP 왕복(수 초)이 도는 동안 DB 트랜잭션을 붙잡고 있을 이유가 없다.

    실패는 채널의 `last_error` 에 적는다 — 비밀번호가 틀렸거나 서버가 막혔을
    때 조용히 아무 메일도 안 들어오면, 관리자는 "고객이 안 보냈나" 로 읽는다.
    """
    settings = get_settings()
    async with session_scope() as session:
        channels = list(
            (
                await session.execute(
                    select(EmailChannel).where(
                        EmailChannel.is_enabled.is_(True),
                        EmailChannel.archived_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        wanted = [(row.id, dict(row.inbound), row.inbound_password_enc) for row in channels]

    if not wanted:
        return 0

    box = SecretBox(settings.secret_key.get_secret_value(), purpose="desk.email")
    store = ObjectStore(settings)
    handled = 0
    for channel_id, inbound_config, password_enc in wanted:
        try:
            if not password_enc:
                raise ImapError("비밀번호가 설정되지 않았다")
            imap_settings = ImapSettings.parse(inbound_config, box.decrypt(password_enc))
            raws = await fetch_unseen(imap_settings)
        except Exception as exc:
            # `ImapError` 만 잡지 않는다. 복호화 실패·설정 오류도 같은 자리에
            # 적혀야 관리자가 무엇이 막았는지 한 곳에서 본다.
            reason = f"{type(exc).__name__}: {exc}"
            log.error("desk.email.poll_failed", channel_id=str(channel_id), error=reason)
            async with session_scope() as session:
                await _note_poll(session, channel_id, error=reason[:500])
            continue

        for raw in raws:
            # **한 통씩 커밋한다.** 한 통이 죽어도 앞의 것을 잃지 않는다 —
            # 메일은 다시 가져올 수 없다(읽음으로 표시했다).
            async with session_scope() as session:
                channel = await session.get(EmailChannel, channel_id)
                if channel is None:
                    break
                try:
                    parsed = parse_message(raw)
                except EmailError as exc:
                    log.warning(
                        "desk.email.unreadable",
                        channel_id=str(channel_id),
                        error=str(exc)[:200],
                    )
                    continue
                await handle_inbound(
                    session,
                    get_permission_service(),
                    channel=channel,
                    parsed=parsed,
                    raw=raw,
                    store=store,
                )
                handled += 1

        async with session_scope() as session:
            await _note_poll(session, channel_id, error=None)

    if handled:
        log.info("desk.email.polled", messages=handled, channels=len(wanted))
    return handled


async def _note_poll(session: AsyncSession, channel_id: Any, *, error: str | None) -> None:
    """폴링 결과를 채널에 적는다. 성공하면 지난 오류를 지운다.

    **지우는 것이 중요하다.** 고친 뒤에도 오류가 남아 있으면 관리자는 아직
    망가진 줄 알고, 그 메시지를 다시는 믿지 않게 된다.
    """
    channel = await session.get(EmailChannel, channel_id)
    if channel is None:
        return
    channel.last_error = error
    channel.last_polled_at = utcnow()


async def sweep() -> dict[str, int]:
    """주기 실행 진입점. 파이프라인을 한 번씩 돌린다.

    **끝나면 심장박동을 남긴다.** 워커가 죽으면 아무 일도 안 일어나는데
    화면은 멀쩡하다 — 밖에서 그것을 알 수 있는 유일한 방법이다.
    실패해도 남긴다: "돌다가 터졌다" 와 "아예 안 돈다" 는 다른 고장이다.
    """
    started = utcnow()
    try:
        return await _sweep_once(started)
    except Exception as exc:
        await _beat("sweep", started, error=f"{type(exc).__name__}: {exc}")
        raise


async def _sweep_once(started: datetime) -> dict[str, int]:
    processed = await drain_outbox()
    delivered = await deliver_webhooks()
    attachments = await sweep_attachments()
    # **드레인 뒤에 돈다.** 앞에서 방금 걸린 클럭이 이미 위반일 수 있다
    # (업무 시간이 아주 짧은 목표). 순서를 바꾸면 그 위반이 한 주기 늦는다.
    sla_breaches = await sweep_sla()
    # **위반 뒤에 돈다.** 목표 시각에 알림과 조치를 함께 두는 설정이 흔하고,
    # 그때 아웃박스에 "위반했다" 가 "그래서 이걸 했다" 보다 먼저 들어가야
    # 읽는 순서가 일어난 순서와 같다.
    sla_escalations = await sweep_sla_escalations()
    # **메일 수신은 드레인 **앞**이 아니라 뒤다.** 여기서 만든 티켓의
    # `desk.ticket.submitted` 는 다음 주기의 드레인이 집는다 — 같은 주기에
    # 처리하려면 드레인을 두 번 돌려야 하고, 그러면 한 주기의 길이가 IMAP
    # 왕복에 묶인다.
    emails = await poll_email()
    # 스프린트 번다운의 오늘 점 (M5). **되짚어 계산하지 않으므로** 여기서
    # 계속 덮어써야 오늘 값이 살아 있고, 날짜가 바뀌면 그대로 굳는다.
    sprints = await snapshot_active_sprints()
    elapsed = (utcnow() - started).total_seconds()
    # **스프린트는 조건에 안 넣는다.** 도는 스프린트가 하나라도 있으면 매
    # 주기 점을 덮어쓰므로, 조건에 넣으면 이 줄이 30초마다 찍힌다 — "무슨
    # 일이 있었다" 를 뜻하던 줄이 심장박동이 되어 버린다. 값은 찍는다.
    if processed or delivered or attachments or sla_breaches or sla_escalations or emails:
        log.info(
            "worker.sweep",
            outbox=processed,
            webhooks=delivered,
            attachments=attachments,
            sla_breaches=sla_breaches,
            sla_escalations=sla_escalations,
            emails=emails,
            sprints=sprints,
            duration_s=round(elapsed, 2),
        )
    await _beat("sweep", started)
    return {
        "outbox": processed,
        "webhooks": delivered,
        "attachments": attachments,
        "sla_breaches": sla_breaches,
        "sla_escalations": sla_escalations,
        "emails": emails,
        "sprints": sprints,
    }


async def snapshot_active_sprints() -> int:
    """도는 스프린트에 오늘 점을 찍는다. 실패해도 스윕을 죽이지 않는다 —
    번다운 한 점을 놓치는 것과 메일이 안 나가는 것은 무게가 다르다."""
    try:
        async with session_scope() as session:
            return await snapshot_sprints(session)
    except Exception as exc:
        log.error("issues.sprint_snapshot_failed", error=f"{type(exc).__name__}: {exc}")
        return 0


async def _beat(task: str, started: datetime, *, error: str | None = None) -> None:
    """심장박동 한 줄. **자기 실패로 스윕을 죽이지 않는다.**

    지표를 못 남기는 것과 일을 못 하는 것은 다르다 — 여기서 예외가 새면
    DB 가 잠깐 흔들린 순간에 스윕 전체가 실패로 기록된다.
    """
    try:
        async with session_scope() as session:
            await beat(
                session,
                task,
                duration_seconds=(utcnow() - started).total_seconds(),
                error=error,
            )
    except Exception as exc:
        log.warning("worker.heartbeat_failed", task=task, error=f"{type(exc).__name__}: {exc}")


# ── arq 진입점 ──────────────────────────────────────────────────
async def send_digests() -> int:
    """하루치를 한 통으로 묶어 보낸다. 보낸 통 수를 돌려준다.

    매시 돈다. 누구에게 보낼지는 **받는 사람의 지역 시각**이 정한다 — 전 세계
    한 시각에 몰아 보내면 절반에게는 한밤중이다 (notify/digest.py).
    """
    settings = get_settings()
    async with session_scope() as session:
        pending = await digests.collect(session, settings)
        if not pending:
            return 0
        # 보내기 **전에** 표시하고 커밋한다. 발송 중에 워커가 죽으면 한 통을
        # 잃지만, 반대로 하면 같은 요약이 계속 다시 나간다.
        digests.mark_sent(pending)

    sent = await send_all(settings, [d.mail for d in pending])
    log.info("mail.digest", queued=len(pending), sent=sent)
    return sent


# arq 의 WorkerCoroutine 프로토콜은 `(ctx, *args, **kwargs)` 를 요구한다.
# 순수 함수와 분리해 두면 테스트와 CLI 가 arq 를 거치지 않고 그대로 부른다.


async def task_drain_outbox(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await drain_outbox()


async def task_deliver_webhooks(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await deliver_webhooks()


async def task_sweep(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> dict[str, int]:
    return await sweep()


async def task_poll_email(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await poll_email()


async def task_sweep_attachments(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await sweep_attachments()


async def task_send_digests(_ctx: dict[Any, Any], *_a: Any, **_kw: Any) -> int:
    return await send_digests()
