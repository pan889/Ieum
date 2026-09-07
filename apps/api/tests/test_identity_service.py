"""identity 서비스 레이어 테스트.

라우터를 거치지 않고 서비스만 본다. HTTP 로 닿기 어려운 경계(정지 계정,
로그인 실패 누적, 초대 토큰 만료)를 여기서 고정한다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.crypto import PasswordHashingService
from ieum.core.events import EventEnvelope
from ieum.core.exceptions import (
    AuthenticationError,
    ConflictError,
    MFARequiredError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.outbox import OutboxEvent
from ieum.core.time import utcnow
from ieum.modules.identity.handlers import HandlerContext, collect_invite_mail
from ieum.modules.identity.invites import decode_invite_token, encode_invite_token
from ieum.modules.identity.models import AuditLog, LoginAttempt, User, UserSession
from ieum.modules.identity.repository import SessionRepository
from ieum.modules.identity.service import AuthService, MFAService, UserService

PASSWORD = "correct-horse-battery-staple"


def hasher(settings: Settings) -> PasswordHashingService:
    return PasswordHashingService(
        memory_cost=settings.argon2_memory_cost,
        time_cost=settings.argon2_time_cost,
        parallelism=settings.argon2_parallelism,
    )


async def make_user(
    session: AsyncSession,
    settings: Settings,
    *,
    status: str = "active",
    password: str | None = PASSWORD,
) -> User:
    user = User(
        email=f"u-{new_id()}@example.com",
        display_name="Tester",
        status=status,
        password_hash=hasher(settings).hash(password) if password else None,
    )
    session.add(user)
    await session.flush()
    return user


class TestInvite:
    async def test_creates_invited_user_without_password(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await UserService(session, settings).invite(
            email="  NewPerson@Example.COM ", display_name="New Person"
        )
        assert user.email == "newperson@example.com"  # 정규화
        assert user.status == "invited"
        assert user.password_hash is None

    async def test_rejects_duplicate_email_case_insensitively(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = UserService(session, settings)
        await service.invite(email="dup@example.com", display_name="First")
        with pytest.raises(ConflictError) as exc:
            await service.invite(email="DUP@EXAMPLE.COM", display_name="Second")
        assert exc.value.code == "identity.email_taken"

    async def test_publishes_event_to_outbox(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """이벤트는 같은 트랜잭션의 아웃박스로 나간다."""
        user = await UserService(session, settings).invite(
            email="evented@example.com", display_name="E"
        )
        rows = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == user.id)))
            .scalars()
            .all()
        )
        assert [r.event_type for r in rows] == ["identity.user.invited"]
        assert rows[0].published_at is None  # 워커가 처리하기 전

    async def test_invite_mail_carries_a_usable_token(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """이 메일이 없으면 초대받은 사람은 계정을 열 방법이 아예 없다.

        토큰은 아웃박스 페이로드가 아니라 **메일을 만들 때** 만든다. 페이로드에
        실으면 계정을 활성화할 수 있는 자격 증명이 웹훅 본문과 로그에 흩어진다.
        """
        service = UserService(session, settings)
        user = await service.invite(email="mailed@example.com", display_name="Mailed")
        await session.flush()

        mails = await collect_invite_mail(
            HandlerContext(session=session, settings=settings),
            EventEnvelope(
                id=new_id(),
                event_type="identity.user.invited",
                aggregate_type="user",
                aggregate_id=user.id,
                payload={"email": user.email},
            ),
        )
        assert len(mails) == 1
        assert mails[0].to == "mailed@example.com"
        assert mails[0].link is not None
        token = mails[0].link.removeprefix("/invite?token=")
        assert decode_invite_token(token, settings) == user.id
        # 토큰이 이벤트 페이로드에 새어 나가면 안 된다.
        rows = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == user.id)))
            .scalars()
            .all()
        )
        assert "token" not in str(rows[0].payload)

    async def test_invite_mail_speaks_the_invitees_language(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """초대한 사람이 아니라 받는 사람의 언어다 (i18n.md 3절)."""
        user = await UserService(session, settings).invite(
            email="korean@example.com", display_name="한국", locale="ko"
        )
        await session.flush()
        mails = await collect_invite_mail(
            HandlerContext(session=session, settings=settings),
            EventEnvelope(
                id=new_id(),
                event_type="identity.user.invited",
                aggregate_type="user",
                aggregate_id=user.id,
                payload={},
            ),
        )
        assert mails[0].subject == "Ieum 에 초대되었습니다"

    async def test_no_second_invite_after_activation(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """아웃박스가 재시도될 때 옛 초대가 되살아나면 "왜 또 왔지" 가 된다."""
        service = UserService(session, settings)
        user = await service.invite(email="already@example.com", display_name="A")
        await service.activate_with_password(user_id=user.id, password=PASSWORD)
        await session.flush()

        mails = await collect_invite_mail(
            HandlerContext(session=session, settings=settings),
            EventEnvelope(
                id=new_id(),
                event_type="identity.user.invited",
                aggregate_type="user",
                aggregate_id=user.id,
                payload={},
            ),
        )
        assert mails == []

    async def test_other_events_make_no_mail(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        mails = await collect_invite_mail(
            HandlerContext(session=session, settings=settings),
            EventEnvelope(
                id=new_id(),
                event_type="identity.user.suspended",
                aggregate_type="user",
                aggregate_id=new_id(),
                payload={},
            ),
        )
        assert mails == []

    async def test_activation_sets_password_and_status(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = UserService(session, settings)
        invited = await service.invite(email="pending@example.com", display_name="P")
        activated = await service.activate_with_password(user_id=invited.id, password=PASSWORD)
        assert activated.status == "active"
        assert activated.password_hash is not None

    async def test_activation_rejects_short_password(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = UserService(session, settings)
        invited = await service.invite(email="short@example.com", display_name="S")
        with pytest.raises(ValidationError) as exc:
            await service.activate_with_password(user_id=invited.id, password="short")
        assert exc.value.code == "identity.password_too_short"
        assert exc.value.details["min_length"] == settings.password_min_length

    async def test_activation_of_suspended_account_rejected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings, status="suspended")
        with pytest.raises(ConflictError):
            await UserService(session, settings).activate_with_password(
                user_id=user.id, password=PASSWORD
            )

    async def test_unknown_user(self, session: AsyncSession, settings: Settings) -> None:
        with pytest.raises(NotFoundError):
            await UserService(session, settings).activate_with_password(
                user_id=new_id(), password=PASSWORD
            )


class TestInviteToken:
    def test_roundtrip(self, settings: Settings) -> None:
        user_id = new_id()
        assert decode_invite_token(encode_invite_token(user_id, settings), settings) == user_id

    def test_expired_token_rejected(self, settings: Settings) -> None:
        token = encode_invite_token(new_id(), settings, ttl_seconds=-1)
        with pytest.raises(ValidationError) as exc:
            decode_invite_token(token, settings)
        assert exc.value.code == "identity.invite_expired"

    def test_tampered_token_rejected(self, settings: Settings) -> None:
        token = encode_invite_token(new_id(), settings)
        tampered = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
        with pytest.raises(ValidationError) as exc:
            decode_invite_token(tampered, settings)
        assert exc.value.code == "identity.invite_invalid"

    def test_garbage_rejected(self, settings: Settings) -> None:
        with pytest.raises(ValidationError):
            decode_invite_token("not-a-token", settings)


class TestPasswordChange:
    async def test_wrong_current_password_rejected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        with pytest.raises(AuthenticationError):
            await UserService(session, settings).change_password(
                user_id=user.id, current_password="wrong-password", new_password=PASSWORD
            )

    async def test_revokes_all_sessions(self, session: AsyncSession, settings: Settings) -> None:
        """비밀번호 변경은 탈취 대응이다. 다른 기기가 살아 있으면 의미가 없다."""
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        await auth.login(email=user.email, password=PASSWORD, ip="10.0.0.1")
        await auth.login(email=user.email, password=PASSWORD, ip="10.0.0.2")
        await session.flush()
        assert len(await SessionRepository(session).list_live_for_user(user.id)) == 2

        await UserService(session, settings).change_password(
            user_id=user.id, current_password=PASSWORD, new_password="a-brand-new-password"
        )
        await session.flush()
        assert await SessionRepository(session).list_live_for_user(user.id) == []

    async def test_new_password_must_meet_policy(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        with pytest.raises(ValidationError):
            await UserService(session, settings).change_password(
                user_id=user.id, current_password=PASSWORD, new_password="tiny"
            )


class TestLoginGuards:
    @pytest.mark.parametrize("status", ["invited", "suspended"])
    async def test_non_active_accounts_cannot_log_in(
        self, session: AsyncSession, settings: Settings, status: str
    ) -> None:
        """상태별로 다른 메시지를 주면 계정 존재가 새어나간다. 전부 같은 예외다."""
        user = await make_user(session, settings, status=status)
        with pytest.raises(AuthenticationError) as exc:
            await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        assert exc.value.code == "auth.unauthenticated"

    async def test_sso_only_account_cannot_use_password(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings, password=None)
        with pytest.raises(AuthenticationError):
            await AuthService(session, settings).login(email=user.email, password=PASSWORD)

    async def test_failures_are_recorded(self, session: AsyncSession, settings: Settings) -> None:
        user = await make_user(session, settings)
        with pytest.raises(AuthenticationError):
            await AuthService(session, settings).login(
                email=user.email, password="nope-nope-nope", ip="10.0.0.9"
            )
        await session.flush()
        attempts = (
            (await session.execute(select(LoginAttempt).where(LoginAttempt.email == user.email)))
            .scalars()
            .all()
        )
        assert [a.succeeded for a in attempts] == [False]

    async def test_rate_limited_after_repeated_failures(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """잠그지 않고 지연시킨다 — 잠그면 타인이 남의 계정을 잠글 수 있다."""
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        for _ in range(settings.login_max_attempts):
            with pytest.raises(AuthenticationError):
                await auth.login(email=user.email, password="wrong-password", ip="10.0.0.9")
            await session.flush()

        with pytest.raises(RateLimitedError) as exc:
            await auth.login(email=user.email, password=PASSWORD, ip="10.0.0.9")
        assert exc.value.retry_after_seconds > 0
        assert exc.value.status_code == 429

    async def test_audit_log_records_success_and_failure(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        with pytest.raises(AuthenticationError):
            await auth.login(email=user.email, password="wrong-password")
        await auth.login(email=user.email, password=PASSWORD)
        await session.flush()

        actions = {
            row.action
            for row in (
                await session.execute(select(AuditLog).where(AuditLog.actor_id == user.id))
            ).scalars()
        }
        assert {"auth.login.failed", "auth.login.succeeded"} <= actions

    async def test_password_is_rehashed_when_parameters_rise(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        # 아주 약한 파라미터로 저장된 기존 해시를 흉내 낸다.
        weak = PasswordHashingService(memory_cost=8, time_cost=1, parallelism=1)
        user.password_hash = weak.hash(PASSWORD)
        original = user.password_hash
        await session.flush()

        await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        assert user.password_hash != original


class TestSessionLifecycle:
    async def test_logout_revokes_only_that_session(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        first = await auth.login(email=user.email, password=PASSWORD)
        await auth.login(email=user.email, password=PASSWORD)
        await session.flush()

        await auth.logout(session_id=first.session_id)
        await session.flush()

        live = await SessionRepository(session).list_live_for_user(user.id)
        assert [s.id for s in live] != []
        assert first.session_id not in {s.id for s in live}

    async def test_logout_is_idempotent(self, session: AsyncSession, settings: Settings) -> None:
        user = await make_user(session, settings)
        issued = await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        await session.flush()
        auth = AuthService(session, settings)
        await auth.logout(session_id=issued.session_id)
        await auth.logout(session_id=issued.session_id)  # 두 번째는 조용히 무시
        await auth.logout(session_id=new_id())  # 없는 세션도 예외 없이

    async def test_expired_refresh_token_rejected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        issued = await auth.login(email=user.email, password=PASSWORD)
        await session.flush()

        row = await session.get(UserSession, issued.session_id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.flush()

        with pytest.raises(AuthenticationError, match="만료"):
            await auth.refresh(refresh_token=issued.refresh_token)

    async def test_unknown_refresh_token_rejected(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        with pytest.raises(AuthenticationError):
            await AuthService(session, settings).refresh(refresh_token="never-issued")

    async def test_access_token_expiry_is_enforced(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        issued = await auth.login(email=user.email, password=PASSWORD)
        await session.flush()

        row = await session.get(UserSession, issued.session_id)
        assert row is not None
        row.access_expires_at = utcnow() - timedelta(seconds=1)
        await session.flush()

        with pytest.raises(AuthenticationError) as exc:
            await auth.authenticate_access_token(issued.access_token)
        assert exc.value.code == "auth.access_token_expired"

    async def test_suspended_after_login_blocks_access_token(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """즉시 무효화. 서버 상태 세션을 쓰는 이유가 이것이다."""
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        issued = await auth.login(email=user.email, password=PASSWORD)
        await session.flush()

        user.status = "suspended"
        await session.flush()

        with pytest.raises(AuthenticationError):
            await auth.authenticate_access_token(issued.access_token)

    async def test_revoke_all_returns_count(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        for _ in range(3):
            await auth.login(email=user.email, password=PASSWORD)
        await session.flush()
        assert await auth.revoke_all_sessions(user_id=user.id) == 3


class TestMFAService:
    async def test_backup_code_reissue_replaces_old_ones(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        mfa = MFAService(session, settings)
        first = await mfa.issue_backup_codes(user_id=user.id, mfa_satisfied=True)
        second = await mfa.issue_backup_codes(user_id=user.id, mfa_satisfied=True)
        assert set(first).isdisjoint(second)
        assert len(second) == 10

    async def test_backup_codes_need_satisfied_mfa(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        with pytest.raises(MFARequiredError):
            await MFAService(session, settings).issue_backup_codes(
                user_id=user.id, mfa_satisfied=False
            )

    async def test_enrollment_returns_usable_secret_and_qr(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        import pyotp

        user = await make_user(session, settings)
        enrollment = await MFAService(session, settings).start_totp_enrollment(
            user_id=user.id, mfa_satisfied=True
        )
        assert enrollment.provisioning_uri.startswith("otpauth://totp/Ieum:")
        assert "<svg" in enrollment.qr_svg
        # 시크릿이 실제로 TOTP 로 쓸 수 있어야 한다.
        assert len(pyotp.TOTP(enrollment.secret).now()) == 6

    async def test_confirm_rejects_other_users_credential(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        owner = await make_user(session, settings)
        stranger = await make_user(session, settings)
        mfa = MFAService(session, settings)
        enrollment = await mfa.start_totp_enrollment(user_id=owner.id, mfa_satisfied=True)
        await session.flush()

        with pytest.raises(NotFoundError):
            await mfa.confirm_totp_enrollment(
                user_id=stranger.id,
                credential_id=enrollment.credential_id,
                code="000000",
                mfa_satisfied=True,
            )

    async def test_confirm_twice_rejected(self, session: AsyncSession, settings: Settings) -> None:
        import pyotp

        user = await make_user(session, settings)
        mfa = MFAService(session, settings)
        enrollment = await mfa.start_totp_enrollment(user_id=user.id, mfa_satisfied=True)
        await session.flush()
        await mfa.confirm_totp_enrollment(
            user_id=user.id,
            credential_id=enrollment.credential_id,
            code=pyotp.TOTP(enrollment.secret).now(),
            mfa_satisfied=True,
        )
        with pytest.raises(ConflictError):
            await mfa.confirm_totp_enrollment(
                user_id=user.id,
                credential_id=enrollment.credential_id,
                code=pyotp.TOTP(enrollment.secret).now(),
                mfa_satisfied=True,
            )

    async def test_enrollment_for_unknown_user(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        with pytest.raises(NotFoundError):
            await MFAService(session, settings).start_totp_enrollment(
                user_id=new_id(), mfa_satisfied=True
            )

    async def test_verify_requires_live_session(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        with pytest.raises(AuthenticationError):
            await MFAService(session, settings).verify(
                user_id=user.id, session_id=new_id(), code="000000"
            )
