"""워커 파이프라인: 아웃박스 → 알림·웹훅 큐 → 전송.

이 경로가 비어 있으면 이벤트가 조용히 쌓이기만 한다(실제로 그런 상태였다).

워커는 요청 컨텍스트가 없어 `session_scope()` 로 전역 세션을 연다. 그래서
롤백 픽스처로 격리되지 않고, 테스트가 직접 정리한다. 설정은 바꾸지 않는다 —
`get_settings` 는 lru_cache 라 `__wrapped__` 를 갈아끼워도 캐시된 원본이
호출된다. 워커가 실제 설정을 쓰는 그대로 두고, 시크릿도 같은 설정으로 만든다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.core.ids import new_id
from ieum.core.outbox import OutboxEvent
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.notify.models import Notification, Webhook, WebhookDelivery
from ieum.modules.org.models import Project
from ieum.worker.tasks import deliver_webhooks, drain_outbox, sweep

pytestmark = pytest.mark.integration

#: 정적 문장으로 둔다. 테이블명을 문자열로 조립하면 lint 가(옳게) 의심한다.
_CLEANUP = (
    text('DELETE FROM "webhook_delivery"'),
    text('DELETE FROM "webhook"'),
    text('DELETE FROM "notification"'),
    text('DELETE FROM "outbox_event"'),
)


@pytest_asyncio.fixture
async def worker_env(engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """워커가 도는 환경을 흉내낸다 — **기동 배선까지.**

    스윕이 이슈를 만들기 때문에(반복 이슈, A27) 권한 관문이 꽂혀 있어야 한다.
    실제 워커는 `worker.settings.startup` 에서 같은 함수를 부른다. 여기서 빼
    두면 시험만 통과하고 실제 워커는 안 도는 상태가 될 수 있다.
    """
    from ieum.config import get_settings
    from ieum.db import session as session_module
    from ieum.wiring import install_permissions

    install_permissions(get_settings())
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]
    saved_factory = session_module._session_factory
    saved_engine = session_module._engine
    session_module._session_factory = factory
    session_module._engine = engine  # type: ignore[assignment]

    async with factory() as s:
        for statement in _CLEANUP:
            await s.execute(statement)
        await s.commit()

    yield factory

    session_module._session_factory = saved_factory
    session_module._engine = saved_engine


def _encrypt_secret() -> str:
    """워커가 복호화할 수 있어야 하므로 워커와 같은 설정으로 암호화한다."""
    from ieum.config import get_settings
    from ieum.core.crypto import SecretBox

    return SecretBox(
        get_settings().secret_key.get_secret_value(), purpose="notify.webhook"
    ).encrypt("webhook-secret-at-least-16")


async def seed_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str = "issue.created",
    with_webhook: bool = False,
    webhook_events: list[str] | None = None,
    webhook_url: str = "http://127.0.0.1:1/never",  # 연결 불가 — 실패 경로용
) -> dict[str, Any]:
    async with factory() as s:
        assignee = User(
            email=f"n-{new_id()}@e.com", display_name="받는이", status="active", locale="ko"
        )
        actor = User(
            email=f"a-{new_id()}@e.com", display_name="행위자", status="active", locale="en"
        )
        project = Project(key=f"W{secrets.token_hex(3).upper()}", name="Worker")
        s.add_all([assignee, actor, project])
        await s.flush()

        if with_webhook:
            s.add(
                Webhook(
                    name=f"hook-{secrets.token_hex(3)}",
                    scope="project",
                    scope_id=project.id,
                    url=webhook_url,
                    secret_enc=_encrypt_secret(),
                    events=webhook_events or [event_type],
                )
            )

        issue_id = new_id()
        s.add(
            OutboxEvent(
                aggregate_type="issue",
                aggregate_id=issue_id,
                event_type=event_type,
                payload={
                    "project_id": str(project.id),
                    "issue_key": f"{project.key}-1",
                    "summary": "워커 확인",
                    "actor_id": str(actor.id),
                    "assignee_id": str(assignee.id),
                },
            )
        )
        await s.commit()
        return {"assignee_id": assignee.id, "actor_id": actor.id, "issue_id": issue_id}


class TestDrainOutbox:
    async def test_marks_events_published(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        await seed_event(worker_env)
        assert await drain_outbox() == 1

        async with worker_env() as s:
            rows = list((await s.execute(select(OutboxEvent))).scalars().all())
        assert len(rows) == 1
        assert rows[0].published_at is not None

    async def test_creates_notification_in_recipient_locale(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        seeded = await seed_event(worker_env)
        await drain_outbox()

        async with worker_env() as s:
            rows = list((await s.execute(select(Notification))).scalars().all())
        assert len(rows) == 1
        assert rows[0].user_id == seeded["assignee_id"]
        # 수신자가 한국어라 한국어로 렌더돼야 한다 (i18n.md 3절).
        assert "생성됨" in rows[0].title

    async def test_actor_gets_no_notification(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        seeded = await seed_event(worker_env)
        await drain_outbox()

        async with worker_env() as s:
            rows = list((await s.execute(select(Notification))).scalars().all())
        assert seeded["actor_id"] not in {r.user_id for r in rows}

    async def test_empty_outbox_is_a_noop(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        assert await drain_outbox() == 0

    async def test_second_run_does_not_reprocess(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """발행 표시가 안 되면 폴링이 같은 행을 영원히 다시 집는다."""
        await seed_event(worker_env)
        assert await drain_outbox() == 1
        assert await drain_outbox() == 0

        async with worker_env() as s:
            rows = list((await s.execute(select(Notification))).scalars().all())
        assert len(rows) == 1  # 알림도 한 번만

    async def test_unhandled_event_type_is_still_published(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """아무도 안 듣는 이벤트를 남겨두면 폴링이 매번 같은 행을 집는다."""
        await seed_event(worker_env, event_type="issue.archived")
        assert await drain_outbox() == 1

        async with worker_env() as s:
            rows = list((await s.execute(select(OutboxEvent))).scalars().all())
            notifications = list((await s.execute(select(Notification))).scalars().all())
        assert rows[0].published_at is not None
        assert notifications == []  # archived 는 알림 대상이 아니다


class TestEscalationNotification:
    """규칙이 **지목한 사람**에게 알림이 간다 (C5).

    이 배선이 이 기능의 전부다: `notify` 규칙이 하는 일은 사람을 부르는 것
    하나뿐이고, `_recipients` 가 `to_user_id` 를 안 읽으면 규칙은 아무 일도
    하지 않는다 — 워처도 담당자도 아닌 사람이 지목된 경우가 그렇다.
    """

    async def test_the_named_person_gets_it(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        async with worker_env() as s:
            called = User(
                email=f"esc-{new_id()}@e.com",
                display_name="팀장",
                status="active",
                locale="ko",
            )
            project = Project(key=f"E{secrets.token_hex(3).upper()}", name="Escalation")
            s.add_all([called, project])
            await s.flush()
            issue_id = new_id()
            s.add(
                OutboxEvent(
                    aggregate_type="issue",
                    aggregate_id=issue_id,
                    event_type="desk.sla.escalated",
                    payload={
                        "project_id": str(project.id),
                        "issue_key": f"{project.key}-1",
                        "summary": "에스컬레이션 확인",
                        "policy_id": str(new_id()),
                        "rule": "75:notify",
                        "action": "notify",
                        "at_percent": 80,
                        # 워처도 담당자도 아니다. 규칙이 지목했을 뿐이다.
                        "to_user_id": str(called.id),
                    },
                )
            )
            await s.commit()
            called_id = called.id

        assert await drain_outbox() == 1
        async with worker_env() as s:
            rows = list((await s.execute(select(Notification))).scalars().all())
        assert [row.user_id for row in rows] == [called_id]
        # 조치마다 제목이 다르다 — "마감이 다가온다" 와 "우선순위를 올렸다" 는
        # 받는 사람이 해야 하는 일이 다르다.
        assert "다가옵니다" in rows[0].title
        # 실제로 몇 %에서 돌았는지가 제목에 있다. 워커가 밀렸으면 75% 규칙이
        # 80% 에서 돈다.
        assert "80" in rows[0].title

    async def test_raising_the_priority_tells_the_assignee(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """`raise_priority` 에는 지목된 사람이 없다. 무엇이 바뀌었는지 알아야
        하는 사람은 담당자다."""
        async with worker_env() as s:
            assignee = User(
                email=f"as-{new_id()}@e.com",
                display_name="담당자",
                status="active",
                locale="ko",
            )
            project = Project(key=f"R{secrets.token_hex(3).upper()}", name="Raise")
            s.add_all([assignee, project])
            await s.flush()
            s.add(
                OutboxEvent(
                    aggregate_type="issue",
                    aggregate_id=new_id(),
                    event_type="desk.sla.escalated",
                    payload={
                        "project_id": str(project.id),
                        "issue_key": f"{project.key}-2",
                        "summary": "우선순위 확인",
                        "policy_id": str(new_id()),
                        "rule": "100:raise_priority",
                        "action": "raise_priority",
                        "at_percent": 100,
                        "priority": 5,
                        "assignee_id": str(assignee.id),
                    },
                )
            )
            await s.commit()
            assignee_id = assignee.id

        await drain_outbox()
        async with worker_env() as s:
            rows = list((await s.execute(select(Notification))).scalars().all())
        assert [row.user_id for row in rows] == [assignee_id]
        assert "올렸습니다" in rows[0].title
        assert "5" in rows[0].title


class TestWebhookQueueing:
    async def test_delivery_row_created_for_subscriber(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        await seed_event(worker_env, with_webhook=True)
        await drain_outbox()

        async with worker_env() as s:
            rows = list((await s.execute(select(WebhookDelivery))).scalars().all())
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].event_type == "issue.created"

    async def test_non_subscribed_event_is_not_queued(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        await seed_event(worker_env, with_webhook=True, webhook_events=["issue.commented"])
        await drain_outbox()

        async with worker_env() as s:
            rows = list((await s.execute(select(WebhookDelivery))).scalars().all())
        assert rows == []


class TestWebhookDelivery:
    async def test_unreachable_endpoint_schedules_retry(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """연결 실패도 재시도 대상이다. 상대 서버가 잠깐 죽었을 수 있다."""
        await seed_event(worker_env, with_webhook=True)
        await drain_outbox()
        assert await deliver_webhooks() == 1

        async with worker_env() as s:
            row = (await s.execute(select(WebhookDelivery))).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.error is not None
        assert row.next_retry_at is not None
        assert row.next_retry_at > utcnow()

    async def test_backoff_prevents_immediate_reattempt(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        await seed_event(worker_env, with_webhook=True)
        await drain_outbox()
        await deliver_webhooks()
        assert await deliver_webhooks() == 0

    async def test_disabled_webhook_delivery_is_abandoned(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        await seed_event(worker_env, with_webhook=True)
        await drain_outbox()

        async with worker_env() as s:
            hook = (await s.execute(select(Webhook))).scalar_one()
            hook.enabled = False
            await s.commit()

        await deliver_webhooks()
        async with worker_env() as s:
            row = (await s.execute(select(WebhookDelivery))).scalar_one()
        assert row.status == "abandoned"

    async def test_undecryptable_secret_does_not_stop_the_batch(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """키 로테이션이나 다른 키로 만든 DB 복원이면 실제로 생긴다.

        하나가 배치 전체를 멈추면 나머지 웹훅이 영원히 안 나간다.
        """
        await seed_event(worker_env, with_webhook=True)
        await drain_outbox()

        async with worker_env() as s:
            hook = (await s.execute(select(Webhook))).scalar_one()
            hook.secret_enc = "not-a-valid-ciphertext"
            await s.commit()

        assert await deliver_webhooks() == 1
        async with worker_env() as s:
            row = (await s.execute(select(WebhookDelivery))).scalar_one()
            hook = (await s.execute(select(Webhook))).scalar_one()
        assert row.status == "abandoned"
        assert "복호화" in (row.error or "")
        # 고칠 때까지 계속 시도하지 않도록 꺼둔다.
        assert hook.enabled is False


class TestOneBadEventDoesNotTakeTheBatchWithIt:
    """**한 건의 DB 오류가 배치 전체를 되돌렸다.**

    행마다 `except` 가 있었지만, DB 오류가 나면 그 세션은 못 쓰는 상태가 된다.
    `except` 가 적으려던 `attempts` 도 배치 끝의 커밋과 함께 통째로 되돌아갔고,
    그래서 (1) 같은 배치의 멀쩡한 이벤트까지 사라졌고 (2) 재시도 횟수가 안 늘어
    `MAX_ATTEMPTS` 상한이 영원히 안 걸렸다 — 같은 행이 배치마다 되돌아와 매번
    다른 이벤트를 끌고 죽었다.
    """

    async def test_a_db_error_is_contained_to_its_own_row(
        self,
        worker_env: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ieum.worker import tasks as worker_tasks

        poison = await seed_event(worker_env)
        healthy = await seed_event(worker_env)

        real = worker_tasks.handle_issue_event

        async def explode(ctx: Any, envelope: Any) -> list[Any]:
            if envelope.aggregate_id == poison["issue_id"]:
                # 진짜 DB 오류여야 한다 — 파이썬 예외만으로는 세션이 안 죽는다.
                await ctx.session.execute(text("SELECT 1 / 0"))
            result: list[Any] = await real(ctx, envelope)
            return result

        monkeypatch.setattr(worker_tasks, "handle_issue_event", explode)
        assert await drain_outbox() == 2

        async with worker_env() as session:
            rows = {
                row.aggregate_id: row
                for row in (await session.execute(select(OutboxEvent))).scalars().all()
            }
        bad = rows[poison["issue_id"]]
        good = rows[healthy["issue_id"]]

        # 멀쩡한 쪽은 그대로 발행됐다. 여기가 예전에 같이 사라지던 자리다.
        assert good.published_at is not None
        # 죽은 쪽은 안 발행됐고, **재시도 횟수가 늘었다** — 상한이 걸린다.
        assert bad.published_at is None
        assert bad.attempts == 1
        assert bad.last_error


class TestOneBadMailDoesNotEatTheRest:
    """**읽음으로 표시된 메일은 다시 못 가져온다.**

    한 통씩 커밋하는 자리에 "읽을 수 없는 메일" 만 감싸 두었다. 읽히기는
    하는데 처리에서 죽는 메일이 있으면 예외가 반복문 밖으로 나가, 아직 안 본
    나머지가 통째로 사라졌다 — 바로 위 주석이 "한 통이 죽어도 앞의 것을 잃지
    않는다" 고 약속하는 그 자리다.
    """

    async def test_the_later_messages_still_get_handled(
        self,
        worker_env: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ieum.config import get_settings
        from ieum.core.crypto import SecretBox
        from ieum.core.permissions import PermissionService
        from ieum.modules.org.repository import OrgPermissionResolver
        from ieum.worker import tasks as worker_tasks
        from test_desk_inbound import _channel, mail

        permissions = PermissionService(
            resolver=OrgPermissionResolver(), step_up_window_seconds=300
        )
        box = SecretBox(get_settings().secret_key.get_secret_value(), purpose="desk.email")
        async with worker_env() as session:
            channel, _ = await _channel(session, permissions)
            channel.inbound_password_enc = box.encrypt("imap-password")
            await session.commit()

        poison = mail(subject="죽는 메일")
        healthy = mail(subject="살아남는 메일")
        handled: list[bytes] = []

        async def fetch(*_a: object, **_kw: object) -> list[bytes]:
            return [poison, healthy]

        async def handle(*_a: object, **kwargs: object) -> object:
            raw = kwargs["raw"]
            if raw == poison:
                raise RuntimeError("이 통은 처리할 수 없다")
            handled.append(raw)  # type: ignore[arg-type]
            return None

        monkeypatch.setattr(worker_tasks, "fetch_unseen", fetch)
        monkeypatch.setattr(worker_tasks, "handle_inbound", handle)

        # 죽은 한 통은 안 세고, 뒤의 한 통은 그대로 처리된다.
        assert await worker_tasks.poll_email() == 1
        assert handled == [healthy]


class TestOneStageDoesNotStarveTheRest:
    """**앞 단계 하나가 터지면 뒤가 통째로 굶었다.**

    단계는 서로 독립인데 한 줄로 이어져 있었다. 아웃박스가 막히면 SLA 시계도,
    받은 메일도, 반복 이슈도 며칠씩 멈추는데 화면은 멀쩡하다.
    """

    async def test_the_later_stages_still_run(
        self,
        worker_env: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ieum.worker import tasks as worker_tasks

        ran: list[str] = []

        async def boom() -> int:
            raise RuntimeError("드레인이 막혔다")

        def note(name: str, real: Any) -> Any:
            async def wrapped() -> int:
                ran.append(name)
                count: int = await real()
                return count

            return wrapped

        monkeypatch.setattr(worker_tasks, "drain_outbox", boom)
        for name in ("sweep_sla", "poll_email", "run_due_recurrences"):
            monkeypatch.setattr(worker_tasks, name, note(name, getattr(worker_tasks, name)))

        with pytest.raises(worker_tasks.SweepStageError) as exc:
            await sweep()
        assert "outbox" in str(exc.value)
        assert ran == ["sweep_sla", "poll_email", "run_due_recurrences"]

    async def test_the_heartbeat_says_what_broke(
        self,
        worker_env: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """무엇이 터졌는지 안 적으면 "돌다가 터졌다" 만 남는다."""
        from ieum.core.heartbeat import all_beats
        from ieum.worker import tasks as worker_tasks

        async def boom() -> int:
            raise RuntimeError("드레인이 막혔다")

        monkeypatch.setattr(worker_tasks, "drain_outbox", boom)
        with pytest.raises(worker_tasks.SweepStageError):
            await sweep()

        async with worker_env() as session:
            rows = await all_beats(session)
        assert rows[0].last_error is not None
        assert "outbox" in rows[0].last_error


class TestSweep:
    """스윕이 무엇을 돌리는지 **정확한 사전으로** 못 박는다.

    파이프라인을 더하면 이 시험이 붉어지는 것이 의도다 — 더한 사람이 그것을
    보고 여기 적어야 하고, 그러면 스윕이 무엇을 하는지 한 자리에 남는다.
    SLA 위반 스윕(C4)·에스컬레이션 스윕(C5)·메일 수신(C6)을 더할 때 실제로
    붉어졌다. 세 번 다 이 시험이 먼저 알려 줬다.
    """

    async def test_runs_both_pipelines(self, worker_env: async_sessionmaker[AsyncSession]) -> None:
        await seed_event(worker_env, with_webhook=True)
        assert await sweep() == {
            "outbox": 1,
            "webhooks": 1,
            "attachments": 0,
            "sla_breaches": 0,
            "sla_escalations": 0,
            "emails": 0,
            "sprints": 0,
            "recurrences": 0,
        }

    async def test_it_leaves_a_heartbeat(
        self, worker_env: async_sessionmaker[AsyncSession]
    ) -> None:
        """**워커가 죽으면 아무 일도 안 일어나는데 화면은 멀쩡하다.**

        밖에서 그것을 알 수 있는 유일한 방법이 이 한 줄이다 — 없으면 로그를
        읽는 사람만 알 수 있고, 아무도 안 읽는다.
        """
        from ieum.core.heartbeat import all_beats

        await sweep()
        async with worker_env() as session:
            rows = await all_beats(session)
        assert [row.task for row in rows] == ["sweep"]
        assert rows[0].last_error is None
        assert rows[0].duration_seconds >= 0

    async def test_idle_sweep_is_cheap(self, worker_env: async_sessionmaker[AsyncSession]) -> None:
        assert await sweep() == {
            "outbox": 0,
            "webhooks": 0,
            "attachments": 0,
            "sla_breaches": 0,
            "sla_escalations": 0,
            "emails": 0,
            "sprints": 0,
            "recurrences": 0,
        }
