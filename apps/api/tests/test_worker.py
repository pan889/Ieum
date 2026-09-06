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
    from ieum.db import session as session_module

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


class TestSweep:
    async def test_runs_both_pipelines(self, worker_env: async_sessionmaker[AsyncSession]) -> None:
        await seed_event(worker_env, with_webhook=True)
        assert await sweep() == {"outbox": 1, "webhooks": 1, "attachments": 0}

    async def test_idle_sweep_is_cheap(self, worker_env: async_sessionmaker[AsyncSession]) -> None:
        assert await sweep() == {"outbox": 0, "webhooks": 0, "attachments": 0}
