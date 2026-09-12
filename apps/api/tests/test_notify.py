"""알림·워치·웹훅.

서명·백오프는 순수 함수라 DB 없이, 팬아웃과 파이프라인은 실제 DB 로 본다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.events import EventEnvelope
from ieum.core.exceptions import PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.notify import delivery as wd
from ieum.modules.notify import digest as nd
from ieum.modules.notify import permissions as nperms
from ieum.modules.notify.handlers import (
    HandlerContext,
    enqueue_webhooks,
    handle_issue_event,
    handle_page_event,
)
from ieum.modules.notify.models import (
    Notification,
    NotificationPreference,
    Webhook,
    WebhookDelivery,
)
from ieum.modules.notify.repository import DeliveryRepository
from ieum.modules.notify.service import (
    MAX_WATCH_TARGETS,
    NotificationRequest,
    NotificationService,
    WatchService,
    WebhookService,
)
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

WEBHOOK_SECRET = "webhook-secret-at-least-16"


# ── 서명·백오프 (DB 불필요) ──────────────────────────────────────


class TestSigning:
    def test_signature_is_deterministic(self) -> None:
        body = b'{"a":1}'
        assert wd.sign("s3cret", "100", body) == wd.sign("s3cret", "100", body)

    def test_signature_matches_reference_hmac(self) -> None:
        """수신자가 표준 HMAC 으로 검증할 수 있어야 한다."""
        body = b'{"a":1}'
        expected = hmac.new(b"s3cret", b"100." + body, hashlib.sha256).hexdigest()
        assert wd.sign("s3cret", "100", body) == f"v1={expected}"

    def test_timestamp_is_part_of_the_signature(self) -> None:
        """타임스탬프가 빠지면 같은 본문을 무한히 재전송할 수 있다."""
        body = b'{"a":1}'
        assert wd.sign("s", "100", body) != wd.sign("s", "200", body)

    def test_different_secret_different_signature(self) -> None:
        assert wd.sign("a", "1", b"x") != wd.sign("b", "1", b"x")


class TestBackoff:
    def test_grows_exponentially(self) -> None:
        assert wd.backoff(1) == timedelta(minutes=1)
        assert wd.backoff(2) == timedelta(minutes=2)
        assert wd.backoff(3) == timedelta(minutes=4)

    def test_capped(self) -> None:
        """상한이 없으면 재시도 간격이 며칠 단위로 벌어진다."""
        assert wd.backoff(20) == timedelta(minutes=30)


class TestApplyOutcome:
    def _pair(self) -> tuple[Webhook, WebhookDelivery]:
        webhook = Webhook(name="w", scope="global", url="http://x", secret_enc="e", events=[])
        delivery = WebhookDelivery(
            webhook_id=new_id(), event_id=new_id(), event_type="issue.created", payload={}
        )
        return webhook, delivery

    def test_success_marks_delivered_and_clears_failures(self) -> None:
        webhook, delivery = self._pair()
        webhook.consecutive_failures = 5
        wd.apply_outcome(webhook, delivery, wd.DeliveryOutcome(ok=True, status_code=200))
        assert delivery.status == "delivered"
        assert delivery.delivered_at is not None
        assert delivery.next_retry_at is None
        assert webhook.consecutive_failures == 0

    def test_failure_schedules_retry(self) -> None:
        webhook, delivery = self._pair()
        wd.apply_outcome(webhook, delivery, wd.DeliveryOutcome(ok=False, status_code=500))
        assert delivery.status == "pending"
        assert delivery.attempts == 1
        assert delivery.next_retry_at is not None

    def test_gives_up_after_max_attempts(self) -> None:
        webhook, delivery = self._pair()
        delivery.attempts = wd.MAX_ATTEMPTS - 1
        wd.apply_outcome(webhook, delivery, wd.DeliveryOutcome(ok=False, status_code=500))
        assert delivery.status == "abandoned"
        assert delivery.next_retry_at is None

    def test_auto_disables_after_repeated_failures(self) -> None:
        """죽은 엔드포인트를 영원히 두드리지 않는다."""
        webhook, delivery = self._pair()
        webhook.consecutive_failures = wd.DISABLE_AFTER_FAILURES - 1
        wd.apply_outcome(webhook, delivery, wd.DeliveryOutcome(ok=False, status_code=500))
        assert webhook.enabled is False
        assert webhook.disabled_reason is not None

    @pytest.mark.parametrize("code", [301, 302, 404, 500])
    def test_only_2xx_counts_as_delivered(self, code: int) -> None:
        webhook, delivery = self._pair()
        wd.apply_outcome(webhook, delivery, wd.DeliveryOutcome(ok=False, status_code=code))
        assert delivery.status != "delivered"


# ── DB 기반 ──────────────────────────────────────────────────────


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


@pytest_asyncio.fixture
async def people(session: AsyncSession) -> AsyncIterator[dict[str, User]]:
    korean = User(email=f"k-{new_id()}@e.com", display_name="한국", status="active", locale="ko")
    english = User(
        email=f"e-{new_id()}@e.com", display_name="English", status="active", locale="en"
    )
    actor = User(email=f"a-{new_id()}@e.com", display_name="Actor", status="active", locale="en")
    session.add_all([korean, english, actor])
    await session.flush()
    yield {"korean": korean, "english": english, "actor": actor}


class TestFanOut:
    async def test_renders_in_each_recipient_locale(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """발신자 언어가 아니라 **수신자** 언어로 렌더한다 (i18n.md 3절)."""
        service = NotificationService(session, settings)
        created = await service.fan_out(
            {people["korean"].id, people["english"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "ENG-1", "summary": "제목"},
            ),
        )
        by_user = {n.user_id: n.title for n in created}
        assert by_user[people["korean"].id] == "ENG-1 생성됨: 제목"
        assert by_user[people["english"].id] == "ENG-1 created: 제목"

    async def test_actor_excluded(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """자기 행동을 자기에게 알리지 않는다."""
        created = await NotificationService(session, settings).fan_out(
            {people["actor"].id, people["korean"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                actor_id=people["actor"].id,
                params={"key": "X-1", "summary": "s"},
            ),
        )
        assert {n.user_id for n in created} == {people["korean"].id}

    async def test_the_actor_can_ask_to_be_told(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """자기 행동을 안 알리는 것은 **기본값이지 규칙이 아니다.**

        `notify_own_actions` 는 API 도 화면도 내놓는 설정인데, 전에는 여기서
        액터를 무조건 빼서 **저장만 되고 아무 일도 하지 않았다.** 스키마·모델·
        마이그레이션에만 있고 어떤 로직도 그 값을 읽지 않았다.
        """
        session.add(NotificationPreference(user_id=people["actor"].id, notify_own_actions=True))
        await session.flush()
        created = await NotificationService(session, settings).fan_out(
            {people["actor"].id, people["korean"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                actor_id=people["actor"].id,
                params={"key": "X-1", "summary": "s"},
            ),
        )
        assert {n.user_id for n in created} == {people["actor"].id, people["korean"].id}

    async def test_the_actor_who_asked_still_obeys_the_other_switches(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """켜 두었어도 인앱을 끄면 안 온다 — 두 설정이 서로를 무시하지 않는다."""
        session.add(
            NotificationPreference(
                user_id=people["actor"].id, notify_own_actions=True, in_app=False
            )
        )
        await session.flush()
        created = await NotificationService(session, settings).fan_out(
            {people["actor"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                actor_id=people["actor"].id,
                params={"key": "X-1", "summary": "s"},
            ),
        )
        assert created == []

    async def test_respects_in_app_preference(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        session.add(NotificationPreference(user_id=people["korean"].id, in_app=False))
        await session.flush()
        created = await NotificationService(session, settings).fan_out(
            {people["korean"].id, people["english"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X-1", "summary": "s"},
            ),
        )
        assert {n.user_id for n in created} == {people["english"].id}

    async def test_respects_muted_events(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        session.add(
            NotificationPreference(user_id=people["korean"].id, muted_events=["issue.created"])
        )
        await session.flush()
        created = await NotificationService(session, settings).fan_out(
            {people["korean"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X-1", "summary": "s"},
            ),
        )
        assert created == []

    async def test_inactive_user_skipped(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        people["korean"].status = "suspended"
        await session.flush()
        created = await NotificationService(session, settings).fan_out(
            {people["korean"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X", "summary": "s"},
            ),
        )
        assert created == []

    async def test_mail_respects_email_preference(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        session.add(NotificationPreference(user_id=people["korean"].id, email_mode="off"))
        await session.flush()
        service = NotificationService(session, settings)
        created = await service.fan_out(
            {people["korean"].id, people["english"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X", "summary": "s"},
            ),
        )
        mails = await service.pending_mail(created)
        assert [m.to for m in mails] == [people["english"].email]


class TestNotificationApi:
    async def test_unread_count_and_mark_read(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["korean"]
        actor = Actor(user_id=user.id, email=user.email, is_active=True, locale="ko")
        service = NotificationService(session, settings)
        for _ in range(3):
            await service.fan_out(
                {user.id},
                NotificationRequest(
                    kind="issue.created",
                    title_key="notifications:issue.created",
                    params={"key": "X", "summary": "s"},
                ),
            )
        await session.flush()

        assert await service.unread_count(actor) == 3
        assert await service.mark_read(actor) == 3
        assert await service.unread_count(actor) == 0

    async def test_cannot_read_someone_elses(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        service = NotificationService(session, settings)
        await service.fan_out(
            {people["korean"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X", "summary": "s"},
            ),
        )
        await session.flush()

        stranger = Actor(
            user_id=people["english"].id, email=people["english"].email, is_active=True
        )
        assert await service.mark_read(stranger) == 0
        korean_actor = Actor(
            user_id=people["korean"].id, email=people["korean"].email, is_active=True
        )
        assert await service.unread_count(korean_actor) == 1

    async def test_list_only_own(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        service = NotificationService(session, settings)
        await service.fan_out(
            {people["korean"].id, people["english"].id},
            NotificationRequest(
                kind="issue.created",
                title_key="notifications:issue.created",
                params={"key": "X", "summary": "s"},
            ),
        )
        await session.flush()
        actor = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        page = await service.list_for(actor, PageRequest(limit=50))
        assert {n.user_id for n in page.items} == {people["korean"].id}


class TestWatch:
    async def test_watch_and_unwatch(self, session: AsyncSession, people: dict[str, User]) -> None:
        user = people["korean"]
        actor = Actor(user_id=user.id, email=user.email, is_active=True)
        service = WatchService(session)
        target = new_id()

        assert await service.watch(actor, "issue", target) is True
        # 두 번째는 False 지만 에러는 아니다. 중복 요청이 에러면 UI 가 번거롭다.
        assert await service.watch(actor, "issue", target) is False
        assert await service.is_watching(actor, "issue", target) is True
        assert await service.unwatch(actor, "issue", target) is True
        assert await service.is_watching(actor, "issue", target) is False

    async def test_invalid_target_rejected(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        actor = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        with pytest.raises(ValidationError) as exc:
            await WatchService(session).watch(actor, "nonsense", new_id())
        assert exc.value.code == "notify.invalid_watch_target"

    async def test_watchers_of_returns_all(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        target = new_id()
        service = WatchService(session)
        for user in (people["korean"], people["english"]):
            await service.watch(
                Actor(user_id=user.id, email=user.email, is_active=True), "issue", target
            )
        await session.flush()
        assert await service.watchers_of("issue", target) == {
            people["korean"].id,
            people["english"].id,
        }

    async def test_watching_among_answers_many_at_once(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        """**목록 화면이 행마다 묻지 않게 한다.**

        한 페이지가 스무 줄이면 `/status` 로는 질의가 스무 번이다. 그 무름은
        `ProjectPicker` 에 세 번 데고 적어 둔 것과 같은 종류다.
        """
        mine, theirs, untouched = new_id(), new_id(), new_id()
        me = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        service = WatchService(session)
        await service.watch(me, "project", mine)
        await service.watch(me, "project", theirs)
        await session.flush()

        assert await service.watching_among(me, "project", [mine, untouched]) == {mine}
        assert await service.watching_among(me, "project", [mine, theirs]) == {mine, theirs}

    async def test_watching_among_does_not_leak_other_people(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        """**남이 구독한 것을 내 것으로 돌려주지 않는다.**

        `WHERE user_id` 를 빠뜨리면 화면은 "구독 중" 이라고 그리는데 알림은
        그 사람에게 가고 나에게는 안 온다 — 화면과 서버가 갈린다.
        """
        target = new_id()
        service = WatchService(session)
        await service.watch(
            Actor(user_id=people["english"].id, email=people["english"].email, is_active=True),
            "project",
            target,
        )
        await session.flush()

        me = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        assert await service.watching_among(me, "project", [target]) == set()

    async def test_watching_among_does_not_cross_target_kinds(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        """같은 id 가 이슈에도 프로젝트에도 있을 수 있다. 종류를 안 보면
        이슈를 구독한 사람이 프로젝트도 구독한 것이 된다."""
        target = new_id()
        me = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        service = WatchService(session)
        await service.watch(me, "issue", target)
        await session.flush()

        assert await service.watching_among(me, "project", [target]) == set()

    async def test_watching_among_refuses_too_many_at_once(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        """**상한이 없으면 주소 줄과 `IN (...)` 이 같이 길어진다.**

        이 검사는 라우터가 아니라 서비스에 있다 — "한 번에 너무 많이 묻지
        마라" 는 HTTP 의 관심사가 아니고, 라우터에 두면 다른 호출자는 그냥
        지나간다.
        """
        me = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        service = WatchService(session)
        just_enough = [new_id() for _ in range(MAX_WATCH_TARGETS)]
        assert await service.watching_among(me, "project", just_enough) == set()

        with pytest.raises(ValidationError):
            await service.watching_among(me, "project", [*just_enough, new_id()])

    async def test_watching_among_rejects_an_unknown_kind(
        self, session: AsyncSession, people: dict[str, User]
    ) -> None:
        me = Actor(user_id=people["korean"].id, email=people["korean"].email, is_active=True)
        with pytest.raises(ValidationError):
            await WatchService(session).watching_among(me, "sprint", [new_id()])


class TestWebhookService:
    async def _admin(
        self, session: AsyncSession, user: User, project: Project | None = None
    ) -> Actor:
        repo = RoleRepository(session)
        scope = Scope.project(project.id) if project else Scope.global_()
        role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind=scope.kind.value)
        repo.add(role)
        await session.flush()
        for permission in nperms.ALL:
            repo.grant(role.id, permission)
        repo.assign(role_id=role.id, scope=scope, principal_kind="user", principal_id=user.id)
        await session.flush()
        return Actor(
            user_id=user.id,
            email=user.email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
            mfa_verified=True,
        )

    async def test_requires_permission(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        actor = Actor(
            user_id=people["korean"].id,
            email=people["korean"].email,
            is_active=True,
            mfa_satisfied_at=utcnow(),
            # step-up 은 통과시킨다. 여기서 보려는 것은 **권한이 없다** 는
            # 거절이지, 2FA 가 없다는 거절이 아니다.
            mfa_verified=True,
        )
        with pytest.raises(PermissionDeniedError):
            await WebhookService(session, settings, permissions).create(
                actor,
                name="w",
                url="https://example.com/h",
                events=["issue.created"],
                secret=WEBHOOK_SECRET,
            )

    async def test_secret_is_encrypted_not_stored_plainly(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        actor = await self._admin(session, people["korean"])
        row = await WebhookService(session, settings, permissions).create(
            actor,
            name="w",
            url="https://example.com/h",
            events=["issue.created"],
            secret=WEBHOOK_SECRET,
        )
        assert WEBHOOK_SECRET not in row.secret_enc

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x", "/relative"]
    )
    async def test_url_scheme_whitelist(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
        url: str,
    ) -> None:
        """웹훅은 데이터를 외부로 보낸다. 스킴을 열어두면 SSRF 통로가 된다."""
        actor = await self._admin(session, people["korean"])
        with pytest.raises(ValidationError) as exc:
            await WebhookService(session, settings, permissions).create(
                actor, name="w", url=url, events=["issue.created"], secret=WEBHOOK_SECRET
            )
        assert exc.value.code == "notify.invalid_webhook_url"

    async def test_unknown_event_rejected(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        actor = await self._admin(session, people["korean"])
        with pytest.raises(ValidationError) as exc:
            await WebhookService(session, settings, permissions).create(
                actor,
                name="w",
                url="https://example.com/h",
                events=["issue.exploded"],
                secret=WEBHOOK_SECRET,
            )
        assert exc.value.code == "notify.unknown_event_type"

    async def test_empty_event_list_rejected(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        """빈 목록은 '전체 구독'이 아니다. 실수로 아무것도 안 받게 두지 않는다."""
        actor = await self._admin(session, people["korean"])
        with pytest.raises(ValidationError) as exc:
            await WebhookService(session, settings, permissions).create(
                actor, name="w", url="https://x.com/h", events=[], secret=WEBHOOK_SECRET
            )
        assert exc.value.code == "notify.no_events_selected"

    async def test_short_secret_rejected(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        actor = await self._admin(session, people["korean"])
        with pytest.raises(ValidationError):
            await WebhookService(session, settings, permissions).create(
                actor,
                name="w",
                url="https://x.com/h",
                events=["issue.created"],
                secret="short",
            )

    async def test_reenabling_clears_failure_counter(
        self,
        session: AsyncSession,
        settings: Settings,
        permissions: PermissionService,
        people: dict[str, User],
    ) -> None:
        """카운터를 안 지우면 다시 켜자마자 한 번 더 실패하고 꺼진다."""
        actor = await self._admin(session, people["korean"])
        service = WebhookService(session, settings, permissions)
        row = await service.create(
            actor,
            name="w",
            url="https://x.com/h",
            events=["issue.created"],
            secret=WEBHOOK_SECRET,
        )
        row.enabled = False
        row.consecutive_failures = 20
        row.disabled_reason = "연속 실패"
        await session.flush()

        again = await service.set_enabled(actor, row.id, True)
        assert again.enabled is True
        assert again.consecutive_failures == 0
        assert again.disabled_reason is None


class TestHandlers:
    def _envelope(self, event_type: str, **payload: object) -> EventEnvelope:
        return EventEnvelope(
            id=new_id(),
            event_type=event_type,
            aggregate_type="issue",
            aggregate_id=UUID(str(payload.pop("aggregate_id", new_id()))),
            payload=dict(payload),
        )

    async def test_mention_gets_its_own_kind(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """멘션은 워처 알림과 종류가 달라야 사용자가 따로 끌 수 있다."""
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.commented",
                issue_key="X-1",
                summary="제목",
                actor_id=str(people["actor"].id),
                mentioned_ids=[str(people["english"].id)],
            ),
        )
        await session.flush()
        rows = (
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        assert [(n.user_id, n.kind) for n in rows] == [(people["english"].id, "issue.mentioned")]

    async def test_mentioned_watcher_gets_one_notification(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """같은 일로 알림이 두 개 쌓이면 사람들이 알림을 끈다."""
        issue_id = new_id()
        await WatchService(session).watch(
            Actor(
                user_id=people["english"].id,
                email=people["english"].email,
                is_active=True,
            ),
            "issue",
            issue_id,
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.commented",
                aggregate_id=issue_id,
                issue_key="X-1",
                summary="제목",
                actor_id=str(people["actor"].id),
                mentioned_ids=[str(people["english"].id)],
            ),
        )
        await session.flush()
        rows = (
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        mine = [n for n in rows if n.user_id == people["english"].id]
        assert len(mine) == 1
        assert mine[0].kind == "issue.mentioned"

    async def test_a_project_watcher_hears_about_its_issues(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """**프로젝트를 보고 있으면 그 안의 이슈 소식을 받는다.**

        `watch.target_type` 은 처음부터 `project` 를 받았고 API 도 받아서
        저장했는데, 알림 쪽에서 아무도 그것을 안 읽었다. 저장은 되고 목록에도
        보이는데 소식은 하나도 안 오는 상태였다 — 기능이 없는 것보다 나쁘다,
        구독한 사람은 구독했다고 믿으니까.
        """
        project_id = new_id()
        await WatchService(session).watch(
            Actor(
                user_id=people["english"].id,
                email=people["english"].email,
                is_active=True,
            ),
            "project",
            project_id,
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.created",
                issue_key="X-1",
                summary="제목",
                project_id=str(project_id),
                actor_id=str(people["actor"].id),
            ),
        )
        await session.flush()
        rows = (
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        assert [n.user_id for n in rows] == [people["english"].id]

    async def test_another_projects_watcher_hears_nothing(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """**아무 프로젝트나 걸리면 안 된다.** 하나만 구독했는데 전부 오면
        그 사람은 알림을 통째로 끈다."""
        await WatchService(session).watch(
            Actor(
                user_id=people["english"].id,
                email=people["english"].email,
                is_active=True,
            ),
            "project",
            new_id(),
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.created",
                issue_key="X-1",
                summary="제목",
                project_id=str(new_id()),
                actor_id=str(people["actor"].id),
            ),
        )
        assert created == []

    async def test_self_mention_is_not_notified(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.commented",
                issue_key="X-1",
                summary="제목",
                actor_id=str(people["actor"].id),
                mentioned_ids=[str(people["actor"].id)],
            ),
        )
        assert created == []

    async def test_broken_mention_id_does_not_stop_the_event(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """페이로드 하나가 잘못됐다고 아웃박스가 그 자리에서 멈추면 안 된다."""
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.commented",
                issue_key="X-1",
                summary="제목",
                actor_id=str(people["actor"].id),
                assignee_id=str(people["korean"].id),
                mentioned_ids=["not-a-uuid", str(people["english"].id)],
            ),
        )
        await session.flush()
        rows = (
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        assert {n.user_id for n in rows} == {people["korean"].id, people["english"].id}

    async def test_recipients_include_watchers_and_assignee(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        issue_id = new_id()
        await WatchService(session).watch(
            Actor(
                user_id=people["english"].id,
                email=people["english"].email,
                is_active=True,
            ),
            "issue",
            issue_id,
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.created",
                aggregate_id=issue_id,
                issue_key="X-1",
                summary="제목",
                actor_id=str(people["actor"].id),
                assignee_id=str(people["korean"].id),
            ),
        )
        await session.flush()
        rows = (
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        assert {n.user_id for n in rows} == {
            people["english"].id,
            people["korean"].id,
        }

    async def test_internal_comment_makes_no_notification(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """내부 노트가 알림으로 새어나가면 안 된다."""
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            self._envelope(
                "issue.commented",
                issue_key="X-1",
                summary="s",
                is_internal=True,
                actor_id=str(people["actor"].id),
                assignee_id=str(people["korean"].id),
            ),
        )
        assert created == []

    async def test_unmapped_event_is_ignored(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        ctx = HandlerContext(session=session, settings=settings)
        assert await handle_issue_event(ctx, self._envelope("issue.archived")) == []

    async def test_webhook_enqueued_once_per_event(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """아웃박스가 재시도되면 같은 전송이 두 번 만들어질 수 있다."""
        project = Project(key=f"W{secrets.token_hex(3).upper()}", name="Hook")
        session.add(project)
        await session.flush()
        session.add(
            Webhook(
                name="w",
                scope="project",
                scope_id=project.id,
                url="https://example.com/h",
                secret_enc="enc",
                events=["issue.created"],
            )
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        envelope = self._envelope(
            "issue.created", issue_key="X-1", summary="s", project_id=str(project.id)
        )
        assert await enqueue_webhooks(ctx, envelope) == 1
        await session.flush()
        assert await enqueue_webhooks(ctx, envelope) == 0

    async def test_disabled_webhook_not_enqueued(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project = Project(key=f"D{secrets.token_hex(3).upper()}", name="Off")
        session.add(project)
        await session.flush()
        session.add(
            Webhook(
                name="off",
                scope="project",
                scope_id=project.id,
                url="https://example.com/h",
                secret_enc="enc",
                events=["issue.created"],
                enabled=False,
            )
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        count = await enqueue_webhooks(
            ctx,
            self._envelope("issue.created", project_id=str(project.id), summary="s"),
        )
        assert count == 0

    async def test_webhook_only_receives_subscribed_events(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project = Project(key=f"S{secrets.token_hex(3).upper()}", name="Sub")
        session.add(project)
        await session.flush()
        session.add(
            Webhook(
                name="only-created",
                scope="project",
                scope_id=project.id,
                url="https://example.com/h",
                secret_enc="enc",
                events=["issue.created"],
            )
        )
        await session.flush()

        ctx = HandlerContext(session=session, settings=settings)
        assert (
            await enqueue_webhooks(
                ctx,
                self._envelope("issue.commented", project_id=str(project.id), summary="s"),
            )
            == 0
        )


class TestDeliveryQueue:
    async def _webhook(self, session: AsyncSession) -> Webhook:
        row = Webhook(
            name=f"w-{secrets.token_hex(4)}",
            scope="global",
            url="https://example.com/h",
            secret_enc="enc",
            events=["issue.created"],
        )
        session.add(row)
        await session.flush()
        return row

    def _delivery(self, webhook: Webhook, **kwargs: object) -> WebhookDelivery:
        return WebhookDelivery(
            webhook_id=webhook.id,
            event_id=new_id(),
            event_type="issue.created",
            payload={},
            **kwargs,  # type: ignore[arg-type]
        )

    async def test_due_skips_future_retries(self, session: AsyncSession) -> None:
        webhook = await self._webhook(session)
        soon = self._delivery(webhook, next_retry_at=utcnow() + timedelta(minutes=10))
        ready = self._delivery(webhook, next_retry_at=utcnow() - timedelta(minutes=1))
        session.add_all([soon, ready])
        await session.flush()

        ids = {d.id for d in await DeliveryRepository(session).due()}
        assert ready.id in ids
        assert soon.id not in ids

    async def test_never_attempted_is_due_immediately(self, session: AsyncSession) -> None:
        """next_retry_at 이 NULL 이면 첫 시도다. 기다리지 않는다."""
        webhook = await self._webhook(session)
        fresh = self._delivery(webhook)
        session.add(fresh)
        await session.flush()
        assert fresh.id in {d.id for d in await DeliveryRepository(session).due()}

    async def test_delivered_rows_are_not_picked_up(self, session: AsyncSession) -> None:
        webhook = await self._webhook(session)
        row = self._delivery(webhook, status="delivered")
        session.add(row)
        await session.flush()
        assert row.id not in {d.id for d in await DeliveryRepository(session).due()}

    async def test_abandoned_rows_are_not_retried(self, session: AsyncSession) -> None:
        webhook = await self._webhook(session)
        row = self._delivery(webhook, status="abandoned")
        session.add(row)
        await session.flush()
        assert row.id not in {d.id for d in await DeliveryRepository(session).due()}


class TestDigest:
    """하루치를 한 통으로.

    알림마다 메일이 한 통씩 가면 아무도 안 읽고, 결국 통째로 끈다. 끈
    사람에게는 아무것도 못 알린다.
    """

    def _at(self, hour: int) -> datetime:
        return datetime(2026, 9, 7, hour, 0, tzinfo=UTC)

    async def _prefers_daily(
        self, session: AsyncSession, user: User, *, last: datetime | None = None
    ) -> NotificationPreference:
        row = NotificationPreference(user_id=user.id, email_mode="daily", last_digest_at=last)
        session.add(row)
        await session.flush()
        return row

    async def _notify(self, session: AsyncSession, user: User, title: str) -> Notification:
        row = Notification(user_id=user.id, kind="issue.created", title=title)
        session.add(row)
        await session.flush()
        return row

    def test_morning_follows_the_reader(self) -> None:
        """전 세계 한 시각에 몰아 보내면 절반에게는 한밤중이다."""
        eight_utc = self._at(nd.DIGEST_HOUR)
        assert nd.is_morning(eight_utc, "UTC") is True
        assert nd.is_morning(eight_utc, "Asia/Seoul") is False
        # 서울의 아침 여덟 시는 UTC 로 전날 스물세 시다.
        assert nd.is_morning(datetime(2026, 9, 6, 23, 0, tzinfo=UTC), "Asia/Seoul") is True

    def test_unknown_timezone_falls_back(self) -> None:
        """시간대 이름이 틀렸다고 아예 안 보내는 것보다 낫다."""
        assert nd.is_morning(self._at(nd.DIGEST_HOUR), "Mars/Olympus") is True

    async def test_collects_unread_since_last_digest(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["english"]
        await self._prefers_daily(session, user)
        await self._notify(session, user, "첫 번째")
        await self._notify(session, user, "두 번째")

        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        mine = [d for d in found if d.mail.to == user.email]
        assert len(mine) == 1
        assert mine[0].covered == 2
        assert "첫 번째" in mine[0].mail.body
        assert "두 번째" in mine[0].mail.body
        # 목록으로 데려간다. 알림 하나가 아니라 하루치이므로.
        assert mine[0].mail.link == "/notifications"

    async def test_read_notifications_are_left_out(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """이미 읽었으면 화면에서 봤다는 뜻이다. 다시 보내면 "아까 본 것" 목록이다."""
        user = people["english"]
        await self._prefers_daily(session, user)
        seen = await self._notify(session, user, "이미 봤다")
        seen.read_at = utcnow()
        await session.flush()

        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        assert [d for d in found if d.mail.to == user.email] == []

    async def test_nothing_to_say_sends_nothing(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """빈 요약을 매일 보내면 그게 스팸이다."""
        await self._prefers_daily(session, people["english"])
        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        assert [d for d in found if d.mail.to == people["english"].email] == []

    async def test_not_twice_in_a_day(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["english"]
        await self._prefers_daily(session, user, last=self._at(nd.DIGEST_HOUR) - timedelta(hours=2))
        await self._notify(session, user, "새 소식")

        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        assert [d for d in found if d.mail.to == user.email] == []

    async def test_instant_readers_are_not_digested(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["english"]
        session.add(NotificationPreference(user_id=user.id, email_mode="instant"))
        await session.flush()
        await self._notify(session, user, "새 소식")

        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        assert [d for d in found if d.mail.to == user.email] == []

    async def test_digest_speaks_the_readers_language(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["korean"]
        await self._prefers_daily(session, user)
        await self._notify(session, user, "소식")

        found = await nd.collect(session, settings, now=self._at(nd.DIGEST_HOUR))
        mine = next(d for d in found if d.mail.to == user.email)
        assert mine.mail.subject == "Ieum 소식 1건"

    async def test_marking_sent_stops_the_next_run(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        user = people["english"]
        await self._prefers_daily(session, user)
        await self._notify(session, user, "새 소식")

        moment = self._at(nd.DIGEST_HOUR)
        first = await nd.collect(session, settings, now=moment)
        nd.mark_sent([d for d in first if d.mail.to == user.email], now=moment)
        await session.flush()

        again = await nd.collect(session, settings, now=moment)
        assert [d for d in again if d.mail.to == user.email] == []

    async def test_pending_mail_skips_daily_readers(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """즉시 메일과 다이제스트가 둘 다 가면 같은 일로 두 통이 온다."""
        user = people["english"]
        await self._prefers_daily(session, user)
        row = await self._notify(session, user, "새 소식")

        mails = await NotificationService(session, settings).pending_mail([row])
        assert mails == []


class TestPageHandlers:
    """문서 이벤트 → 알림.

    수신자는 문서 워처 + 스페이스 워처다. 스페이스를 보고 있으면 그 안의
    문서를 하나하나 챙기지 않아도 된다 — 트리가 깊어지면 그게 유일하게 쓸
    만한 구독 단위다.
    """

    def _envelope(self, event_type: str, **payload: object) -> EventEnvelope:
        return EventEnvelope(
            id=new_id(),
            event_type=event_type,
            aggregate_type="page",
            aggregate_id=UUID(str(payload.pop("aggregate_id", new_id()))),
            payload=dict(payload),
        )

    def _actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, email=user.email, is_active=True)

    async def _rows(self, session: AsyncSession, ids: list[UUID]) -> list[Notification]:
        await session.flush()
        result = await session.execute(select(Notification).where(Notification.id.in_(ids)))
        return list(result.scalars().all())

    async def test_page_and_space_watchers_both_get_it(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        page_id, space_id = new_id(), new_id()
        watches = WatchService(session)
        await watches.watch(self._actor(people["english"]), "page", page_id)
        await watches.watch(self._actor(people["korean"]), "space", space_id)
        await session.flush()

        created = await handle_page_event(
            HandlerContext(session=session, settings=settings),
            self._envelope(
                "wiki.page.updated",
                aggregate_id=page_id,
                space_id=str(space_id),
                space_key="ENG",
                path="deploy",
                title="배포",
                actor_id=str(people["actor"].id),
            ),
        )
        rows = await self._rows(session, created)
        assert {n.user_id for n in rows} == {people["english"].id, people["korean"].id}
        # 링크는 사람이 주고받는 주소다. id 로 만들면 열어 봐도 어디인지 모른다.
        assert {n.link for n in rows} == {"/wiki/ENG/deploy"}

    async def test_mention_gets_its_own_kind(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """멘션은 워처 알림과 종류가 달라야 사용자가 따로 끌 수 있다."""
        created = await handle_page_event(
            HandlerContext(session=session, settings=settings),
            self._envelope(
                "wiki.page.commented",
                space_id=str(new_id()),
                space_key="ENG",
                path="deploy",
                title="배포",
                actor_id=str(people["actor"].id),
                mentioned_ids=[str(people["korean"].id)],
            ),
        )
        rows = await self._rows(session, created)
        assert [n.kind for n in rows] == ["wiki.mentioned"]

    async def test_mentioned_watcher_gets_one_notification(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """워처이면서 멘션된 사람에게 같은 일로 두 개가 쌓이면 안 된다."""
        page_id = new_id()
        await WatchService(session).watch(self._actor(people["korean"]), "page", page_id)
        await session.flush()

        created = await handle_page_event(
            HandlerContext(session=session, settings=settings),
            self._envelope(
                "wiki.page.updated",
                aggregate_id=page_id,
                space_id=str(new_id()),
                space_key="ENG",
                path="deploy",
                title="배포",
                actor_id=str(people["actor"].id),
                mentioned_ids=[str(people["korean"].id)],
            ),
        )
        rows = await self._rows(session, created)
        assert len(rows) == 1
        assert rows[0].kind == "wiki.mentioned"

    async def test_actor_does_not_notify_self(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        page_id = new_id()
        await WatchService(session).watch(self._actor(people["actor"]), "page", page_id)
        await session.flush()

        created = await handle_page_event(
            HandlerContext(session=session, settings=settings),
            self._envelope(
                "wiki.page.updated",
                aggregate_id=page_id,
                space_id=str(new_id()),
                space_key="ENG",
                path="deploy",
                title="배포",
                actor_id=str(people["actor"].id),
            ),
        )
        assert created == []

    async def test_recipient_locale_decides_the_wording(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """발신자 언어가 아니라 수신자 언어로 렌더한다 (i18n.md 3절)."""
        page_id = new_id()
        watches = WatchService(session)
        await watches.watch(self._actor(people["korean"]), "page", page_id)
        await watches.watch(self._actor(people["english"]), "page", page_id)
        await session.flush()

        created = await handle_page_event(
            HandlerContext(session=session, settings=settings),
            self._envelope(
                "wiki.page.published",
                aggregate_id=page_id,
                space_id=str(new_id()),
                space_key="ENG",
                path="deploy",
                title="배포 절차",
                actor_id=str(people["actor"].id),
            ),
        )
        rows = await self._rows(session, created)
        by_user = {n.user_id: n.title for n in rows}
        assert by_user[people["korean"].id] == "ENG — 새 문서: 배포 절차"
        assert by_user[people["english"].id] == "ENG — new page: 배포 절차"

    async def test_unmapped_event_is_ignored(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        ctx = HandlerContext(session=session, settings=settings)
        assert await handle_page_event(ctx, self._envelope("wiki.page.archived")) == []
        # 이슈 핸들러가 문서 이벤트를 집어삼키면 안 된다.
        assert await handle_issue_event(ctx, self._envelope("wiki.page.updated")) == []


class TestNotificationIdsAreUsable:
    """알림 id 가 실제로 채워져 나오는지.

    회귀 테스트다. SQLAlchemy 의 `default=` 는 INSERT 시점에 적용되므로
    flush 전 id 는 None 이다. 핸들러가 None 목록을 돌려주면 워커가
    `id IN (NULL)` 로 메일 대상을 찾아 **메일이 조용히 안 나간다**.
    알림 행은 멀쩡히 생기기 때문에 개수만 세면 놓친다.
    """

    async def test_handler_returns_real_ids(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            EventEnvelope(
                id=new_id(),
                event_type="issue.created",
                aggregate_type="issue",
                aggregate_id=new_id(),
                payload={
                    "issue_key": "X-1",
                    "summary": "제목",
                    "assignee_id": str(people["korean"].id),
                },
            ),
        )
        assert created, "알림이 만들어져야 한다"
        assert all(i is not None for i in created), "id 가 None 이면 메일이 안 나간다"

    async def test_mail_is_actually_queued_for_those_ids(
        self, session: AsyncSession, settings: Settings, people: dict[str, User]
    ) -> None:
        """워커가 하는 것과 같은 경로: 핸들러가 준 id 로 메일 대상을 다시 찾는다."""
        ctx = HandlerContext(session=session, settings=settings)
        created = await handle_issue_event(
            ctx,
            EventEnvelope(
                id=new_id(),
                event_type="issue.commented",
                aggregate_type="issue",
                aggregate_id=new_id(),
                payload={
                    "issue_key": "X-2",
                    "summary": "s",
                    "assignee_id": str(people["korean"].id),
                },
            ),
        )
        rows = list(
            (await session.execute(select(Notification).where(Notification.id.in_(created))))
            .scalars()
            .all()
        )
        assert len(rows) == len(created)
        mails = await NotificationService(session, settings).pending_mail(rows)
        assert [m.to for m in mails] == [people["korean"].email]


class TestPayloadShape:
    def test_body_is_stable_json(self) -> None:
        """수신자가 서명을 검증하려면 바이트가 정확히 같아야 한다."""
        payload = {"b": 2, "a": 1}
        first = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        second = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        assert first == second
