"""identity 서비스 레이어 테스트.

라우터를 거치지 않고 서비스만 본다. HTTP 로 닿기 어려운 경계(정지 계정,
로그인 실패 누적, 초대 토큰 만료)를 여기서 고정한다.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

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
from ieum.modules.identity.invites import (
    decode_invite,
    decode_invite_token,
    encode_invite_token,
)
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

    async def test_an_accepted_invite_cannot_be_used_again(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """**계정 탈취 경로였다.**

        초대 토큰은 DB 행 없이 암호화만 해서 준다 — 만료 전까지 몇 번이든
        통한다. 상태를 안 보면 3일 전 초대 메일 한 통을 손에 넣은 사람이
        이미 활성화된 계정의 비밀번호를 자기 것으로 갈아 끼울 수 있다.
        본인 세션은 끊기지도 않아 당한 쪽은 눈치채지 못한다.
        """
        service = UserService(session, settings)
        invited = await service.invite(email="alice@example.com", display_name="Alice")
        token = encode_invite_token(invited.id, settings)

        await service.activate_with_password(user_id=invited.id, password=PASSWORD)
        hashed = invited.password_hash

        with pytest.raises(ConflictError) as exc:
            await service.activate_with_password(
                user_id=decode_invite_token(token, settings), password="attacker-pass-1234"
            )
        assert exc.value.code == "identity.invite_already_accepted"
        assert invited.password_hash == hashed
        assert hasher(settings).verify(invited.password_hash or "", PASSWORD)

    async def test_reinvite_opens_the_door_again(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """비밀번호를 잊은 사람이 돌아오는 길. 관리자만 열 수 있다."""
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        user = await service.invite(email="bob@example.com", display_name="Bob")
        await service.activate_with_password(user_id=user.id, password=PASSWORD)

        again = await service.reinvite(actor_id=admin.id, user_id=user.id)
        assert again.status == "invited"
        assert again.password_hash is None

        # 새 초대장은 새 세대를 싣는다(`collect_invite_mail` 이 그렇게 만든다).
        token = encode_invite_token(user.id, settings, generation=again.invite_generation)
        invite = decode_invite(token, settings)
        restored = await service.activate_with_password(
            user_id=invite.user_id,
            password="brand-new-password",
            invite_generation=invite.generation,
        )
        assert restored.status == "active"

    async def test_reinvite_refuses_an_sso_account(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """IdP 계정을 `invited` 로 내리면 SSO 로그인까지 막힌다."""
        admin = await make_user(session, settings)
        sso = await make_user(session, settings, password=None)
        with pytest.raises(ConflictError) as exc:
            await UserService(session, settings).reinvite(actor_id=admin.id, user_id=sso.id)
        assert exc.value.code == "identity.sso_account_cannot_be_reinvited"
        assert sso.status == "active"


class TestSuspendAndReactivate:
    """되살릴 때 **정지 직전 상태로** 돌아간다."""

    async def test_an_sso_account_comes_back_active(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """**두 달 휴직이 영구 퇴사가 됐다.**

        IdP 로 들어오는 계정은 설계상 비밀번호가 없다. 되살릴 상태를
        비밀번호 유무로 갈랐더니 그 계정이 `invited` 로 떨어졌고, SSO 버튼은
        401 을 돌려줬다. 관리자가 다시 눌러도 이미 정지가 아니라 아무 일도
        안 났고, 다시 초대하려 하면 이메일이 있다고 409 였다.
        """
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        sso = await make_user(session, settings, password=None)

        await service.set_status(actor_id=admin.id, user_id=sso.id, suspend=True)
        assert sso.status == "suspended"

        back = await service.set_status(actor_id=admin.id, user_id=sso.id, suspend=False)
        assert back.status == "active"
        assert back.pre_suspend_status is None

    async def test_an_unaccepted_invite_comes_back_invited(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """수락 안 한 사람을 `active` 로 두면 들어올 수단이 없는 활성 계정이 된다."""
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        pending = await service.invite(email="pending-suspend@example.com", display_name="P")

        await service.set_status(actor_id=admin.id, user_id=pending.id, suspend=True)
        back = await service.set_status(actor_id=admin.id, user_id=pending.id, suspend=False)
        assert back.status == "invited"

    async def test_an_old_row_with_nothing_remembered_comes_back_active(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """이 판이 올라오기 전에 정지된 행은 그 열이 비어 있다.

        `active` 로 본다 — 틀렸으면 `reinvite` 로 되돌릴 수 있지만, 반대로
        틀리면(`invited`) IdP 계정은 되돌릴 길이 없다.
        """
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        legacy = await make_user(session, settings, status="suspended", password=None)
        assert legacy.pre_suspend_status is None

        back = await service.set_status(actor_id=admin.id, user_id=legacy.id, suspend=False)
        assert back.status == "active"


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

    def test_carries_the_generation(self, settings: Settings) -> None:
        token = encode_invite_token(new_id(), settings, generation=3)
        assert decode_invite(token, settings).generation == 3

    def test_a_token_from_before_generations_reads_as_zero(self, settings: Settings) -> None:
        """세대 칸이 생기기 전에 나간 메일. 아직 수락 안 한 초대는 그대로 통해야 한다."""
        import json

        from ieum.core.crypto import SecretBox
        from ieum.core.time import in_seconds
        from ieum.modules.identity.invites import INVITE_PURPOSE

        user_id = new_id()
        legacy = SecretBox(settings.secret_key.get_secret_value(), purpose=INVITE_PURPOSE).encrypt(
            json.dumps({"user_id": str(user_id), "expires_at": in_seconds(60).isoformat()})
        )
        got = decode_invite(legacy, settings)
        assert (got.user_id, got.generation) == (user_id, 0)


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


async def _enrolled(session: AsyncSession, settings: Settings) -> tuple[User, str]:
    """TOTP 를 등록해 둔 사람과 그 시크릿."""
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
    await session.flush()
    return user, enrollment.secret


class TestMFAAttemptLimit:
    """2FA 코드도 찍어 볼 수 있는 횟수가 정해져 있다.

    없으면 비밀번호를 아는 사람이 6자리를 끝까지 찍어 본다 — ±1 스텝이라
    한 번에 세 개가 맞아서 생각보다 빨리 뚫린다.
    """

    async def _fail(
        self, session: AsyncSession, settings: Settings, user: User, times: int
    ) -> None:
        mfa = MFAService(session, settings)
        for _ in range(times):
            issued = await AuthService(session, settings).login(email=user.email, password=PASSWORD)
            await session.flush()
            with pytest.raises(ValidationError):
                await mfa.verify(user_id=user.id, session_id=issued.session_id, code="000000")
            await session.flush()

    async def test_past_the_limit_even_the_right_code_is_refused(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """**코드를 보기 전에** 막는다. 보고 나서 막으면 맞는 코드에만 다른
        답이 나가 제한이 무의미해진다."""
        import pyotp

        user, secret = await _enrolled(session, settings)
        await self._fail(session, settings, user, settings.mfa_max_attempts)

        issued = await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        await session.flush()
        right = pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30))
        with pytest.raises(RateLimitedError) as exc:
            await MFAService(session, settings).verify(
                user_id=user.id, session_id=issued.session_id, code=right
            )
        assert exc.value.status_code == 429
        assert exc.value.retry_after_seconds > 0
        row = await session.get(UserSession, issued.session_id)
        assert row is not None and row.mfa_satisfied_at is None

    async def test_new_sessions_do_not_refill_the_attempts(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """세션마다 세면 로그인 한 번에 시도 횟수가 다시 채워진다. `_fail` 은
        시도마다 새로 로그인한다 — 그래도 막혀야 한다."""
        user, _ = await _enrolled(session, settings)
        await self._fail(session, settings, user, settings.mfa_max_attempts)

        issued = await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        await session.flush()
        with pytest.raises(RateLimitedError):
            await MFAService(session, settings).verify(
                user_id=user.id, session_id=issued.session_id, code="000000"
            )

    async def test_backup_codes_sit_behind_the_same_limit(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user, _ = await _enrolled(session, settings)
        codes = await MFAService(session, settings).issue_backup_codes(
            user_id=user.id, mfa_satisfied=True
        )
        await self._fail(session, settings, user, settings.mfa_max_attempts)

        issued = await AuthService(session, settings).login(email=user.email, password=PASSWORD)
        await session.flush()
        with pytest.raises(RateLimitedError):
            await MFAService(session, settings).verify(
                user_id=user.id, session_id=issued.session_id, code=codes[0]
            )

    async def test_one_persons_failures_do_not_block_another(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        import pyotp

        noisy, _ = await _enrolled(session, settings)
        await self._fail(session, settings, noisy, settings.mfa_max_attempts)

        quiet, secret = await _enrolled(session, settings)
        issued = await AuthService(session, settings).login(email=quiet.email, password=PASSWORD)
        await session.flush()
        await MFAService(session, settings).verify(
            user_id=quiet.id,
            session_id=issued.session_id,
            code=pyotp.TOTP(secret).at(utcnow() + timedelta(seconds=30)),
        )


class TestUnknownAccountTiming:
    """없는 계정도 **같은 비용**으로 해시를 검증한다.

    상수 해시(`m=8,t=1,p=1`)를 썼을 때는 없는 계정의 검증이 수 마이크로초,
    있는 계정은 설정된 비용이라 응답 시간만으로 가입 여부가 갈렸다.
    """

    @pytest.mark.parametrize("kind", ["unknown", "sso_only"])
    async def test_the_dummy_hash_uses_the_configured_parameters(
        self,
        session: AsyncSession,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
    ) -> None:
        seen: list[str] = []
        original = PasswordHashingService.verify

        def spy(self: PasswordHashingService, password_hash: str, password: str) -> bool:
            seen.append(password_hash)
            return original(self, password_hash, password)

        monkeypatch.setattr(PasswordHashingService, "verify", spy)
        if kind == "unknown":
            email = f"nobody-{new_id()}@example.com"
        else:
            email = (await make_user(session, settings, password=None)).email

        with pytest.raises(AuthenticationError):
            await AuthService(session, settings).login(email=email, password=PASSWORD)

        assert len(seen) == 1
        assert seen[0].startswith(
            f"$argon2id$v=19$m={settings.argon2_memory_cost},"
            f"t={settings.argon2_time_cost},p={settings.argon2_parallelism}$"
        )


class TestLoginLimitIsNotALockout:
    """제한은 지연이지 잠금이 아니다. **남이 틀린 횟수로 본인이 막히면 잠금이다.**

    이메일만으로 셌을 때는 주소 하나만 알면 어디서든 열 번 틀려 주는 것으로
    본인을 15분씩 계속 막아 둘 수 있었다.
    """

    async def test_failures_from_elsewhere_do_not_lock_the_owner_out(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await make_user(session, settings)
        auth = AuthService(session, settings)
        for _ in range(settings.login_max_attempts + 2):
            with pytest.raises((AuthenticationError, RateLimitedError)):
                await auth.login(email=user.email, password="wrong-password", ip="203.0.113.66")
            await session.flush()

        # 본인은 자기 자리에서 그대로 들어온다.
        issued = await auth.login(email=user.email, password=PASSWORD, ip="198.51.100.7")
        assert issued.access_token

        # 틀린 쪽은 여전히 막혀 있다 — 무차별 대입 방어는 그대로다.
        with pytest.raises(RateLimitedError):
            await auth.login(email=user.email, password=PASSWORD, ip="203.0.113.66")

    async def test_one_place_trying_many_accounts_is_still_cut_off(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """한 곳에서 계정을 바꿔 가며 찔러 보는 것은 IP 기준이 막는다."""
        auth = AuthService(session, settings)
        for _ in range(settings.login_max_attempts):
            with pytest.raises(AuthenticationError):
                await auth.login(
                    email=f"spray-{new_id()}@example.com",
                    password="wrong-password",
                    ip="203.0.113.99",
                )
            await session.flush()

        user = await make_user(session, settings)
        with pytest.raises(RateLimitedError):
            await auth.login(email=user.email, password=PASSWORD, ip="203.0.113.99")


class TestReinviteClosesTheOldDoors:
    """다시 초대하면 **그 전에 나간 열쇠가 전부 죽어야 한다.**

    다시 초대하는 것은 비밀번호를 잊었을 때만이 아니라 계정이 샜을 때의
    대응이기도 하다. 옛 초대 메일과 그 사람이 만들어 둔 API 토큰이 살아
    있으면, 새 비밀번호를 정하는 순간 빠져나간 것이 그대로 다시 열린다.
    """

    async def test_an_invite_mailed_before_the_reinvite_no_longer_works(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        user = await service.invite(email=f"carol-{new_id()}@example.com", display_name="Carol")
        old = decode_invite(
            encode_invite_token(user.id, settings, generation=user.invite_generation), settings
        )
        await service.activate_with_password(
            user_id=old.user_id, password=PASSWORD, invite_generation=old.generation
        )

        await service.reinvite(actor_id=admin.id, user_id=user.id)
        # 상태는 다시 `invited` 다 — 옛 토큰은 상태 검사를 통과한다. 세대가 막는다.
        with pytest.raises(ValidationError) as exc:
            await service.activate_with_password(
                user_id=old.user_id, password="attacker-pass-1234", invite_generation=old.generation
            )
        assert exc.value.code == "identity.invite_expired"
        assert user.status == "invited"
        assert user.password_hash is None

    async def test_the_new_invite_mail_carries_the_new_generation(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = UserService(session, settings)
        admin = await make_user(session, settings)
        user = await service.invite(email=f"dave-{new_id()}@example.com", display_name="Dave")
        await service.activate_with_password(user_id=user.id, password=PASSWORD)
        await service.reinvite(actor_id=admin.id, user_id=user.id)
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
        assert mails[0].link is not None
        fresh = decode_invite(mails[0].link.removeprefix("/invite?token="), settings)
        assert fresh.generation == user.invite_generation == 1
        restored = await service.activate_with_password(
            user_id=fresh.user_id, password="brand-new-password", invite_generation=fresh.generation
        )
        assert restored.status == "active"

    async def test_reinvite_revokes_api_tokens_but_keeps_mfa(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """2FA 는 그대로 둔다 — 비밀번호를 잊은 사람이 돌아오는 정상 경로다."""
        from ieum.modules.identity.models import ApiToken, MFACredential

        user, _ = await _enrolled(session, settings)
        admin = await make_user(session, settings)
        token = ApiToken(user_id=user.id, name="ci", token_hash=f"h-{new_id()}", scopes=[])
        session.add(token)
        await session.flush()

        await UserService(session, settings).reinvite(actor_id=admin.id, user_id=user.id)
        await session.flush()
        await session.refresh(token)
        assert token.revoked_at is not None

        kept = (
            (await session.execute(select(MFACredential).where(MFACredential.user_id == user.id)))
            .scalars()
            .all()
        )
        assert [c.kind for c in kept] == ["totp"]

        logged = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "identity.user.reinvited", AuditLog.target_id == user.id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert logged.audit_metadata["revoked_tokens"] == 1


class TestCursorPages:
    """`(created_at, id)` 커서. **같은 시각의 행이 경계에 걸려도 빠지면 안 된다.**

    파이썬 튜플끼리 `>` 를 쓰면 SQL 이 `created_at > x` 하나만 남는다.
    한 트랜잭션 안의 행은 `now()` 가 같아 created_at 이 전부 같다 — 일괄
    초대·SCIM 밀어넣기·로그인 폭주가 정확히 그 모양이다.
    """

    async def test_users_created_together_all_come_through(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        from ieum.core.pagination import PageRequest
        from ieum.modules.identity.repository import UserRepository

        made = [await make_user(session, settings) for _ in range(5)]
        assert len({u.created_at for u in made}) == 1

        seen: list[UUID] = []
        cursor: str | None = None
        for _ in range(5):
            page = await UserRepository(session).list_page(
                PageRequest(limit=2, cursor=cursor), ids=[u.id for u in made]
            )
            seen.extend(u.id for u in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert sorted(seen) == sorted(u.id for u in made)

    async def test_audit_rows_written_together_all_come_through(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        from ieum.core.pagination import PageRequest
        from ieum.modules.identity.repository import AuditFilter, AuditRepository

        actor = await make_user(session, settings)
        audit = AuditRepository(session)
        made = [audit.record(action="test.burst", actor_id=actor.id) for _ in range(5)]
        await session.flush()

        seen: list[UUID] = []
        cursor: str | None = None
        for _ in range(5):
            page = await audit.list(
                AuditFilter(actor_id=actor.id), PageRequest(limit=2, cursor=cursor)
            )
            seen.extend(r.id for r in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert sorted(seen) == sorted(r.id for r in made)

    @pytest.mark.parametrize(
        "payload",
        [
            {"id": "00000000-0000-0000-0000-000000000000"},
            {"created_at": "2026-01-01T00:00:00+00:00"},
            {"created_at": "not-a-time", "id": "00000000-0000-0000-0000-000000000000"},
            {"created_at": "2026-01-01T00:00:00+00:00", "id": "not-a-uuid"},
            {"created_at": 12, "id": 34},
            # 시간대가 없으면 어느 시간대로 읽을지 드라이버 마음이 된다.
            {"created_at": "2026-01-01T00:00:00", "id": "00000000-0000-0000-0000-000000000000"},
        ],
    )
    async def test_a_broken_cursor_is_refused_not_a_crash(
        self, session: AsyncSession, payload: dict[str, object]
    ) -> None:
        """깨진 커서 하나가 500 이면 안 된다. 클라이언트가 들고 다니는 값이다."""
        from ieum.core.pagination import PageRequest, encode_cursor
        from ieum.modules.identity.repository import AuditFilter, AuditRepository, UserRepository

        request = PageRequest(limit=2, cursor=encode_cursor(payload))
        with pytest.raises(ValidationError) as users:
            await UserRepository(session).list_page(request)
        assert users.value.code == "common.invalid_cursor"
        with pytest.raises(ValidationError) as rows:
            await AuditRepository(session).list(AuditFilter(), request)
        assert rows.value.code == "common.invalid_cursor"


class TestRefreshRace:
    """같은 리프레시 토큰이 **동시에** 두 번 오면 한 번만 돈다.

    잠그지 않던 때는 두 요청이 같이 "아직 안 쓰인 토큰" 을 읽고 둘 다 새
    세션을 받았다 — 탈취한 쪽과 본인이 함께 살아남고 재사용 탐지는 울리지
    않았다. 두 연결이 진짜로 겹쳐야 보이므로 롤백 세션이 아니라 엔진에서
    커밋하는 세션 둘을 쓴다.
    """

    async def test_the_second_one_waits_and_is_treated_as_reuse(
        self, engine: object, settings: Settings
    ) -> None:
        import asyncio

        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[call-overload]
        async with factory() as setup:
            user = await make_user(setup, settings)
            issued = await AuthService(setup, settings).login(email=user.email, password=PASSWORD)
            await setup.commit()

        async def refresh_like_the_router(s: AsyncSession) -> object:
            try:
                return await AuthService(s, settings).refresh(refresh_token=issued.refresh_token)
            except Exception:
                await s.commit()  # 라우터처럼 실패 경로도 커밋한다(계열 폐기가 남게).
                raise

        try:
            async with factory() as first, factory() as second:
                won = await AuthService(first, settings).refresh(refresh_token=issued.refresh_token)
                # 첫 요청이 커밋하기 전에 두 번째가 온다.
                late = asyncio.create_task(refresh_like_the_router(second))
                await asyncio.sleep(0.3)
                assert not late.done(), "두 번째 요청이 잠금을 기다리지 않고 지나갔다"
                await first.commit()

                with pytest.raises(AuthenticationError, match="재사용"):
                    await late

            # 재사용으로 처리됐으니 이긴 쪽의 세션도 함께 죽었다.
            async with factory() as check:
                survivor = await check.get(UserSession, won.session_id)
                assert survivor is not None and survivor.revoked_at is not None
        finally:
            async with factory() as cleanup:
                await cleanup.execute(delete(AuditLog).where(AuditLog.actor_id == user.id))
                await cleanup.execute(
                    delete(OutboxEvent).where(OutboxEvent.aggregate_id == user.id)
                )
                await cleanup.execute(delete(LoginAttempt).where(LoginAttempt.email == user.email))
                await cleanup.execute(delete(User).where(User.id == user.id))
                await cleanup.commit()
