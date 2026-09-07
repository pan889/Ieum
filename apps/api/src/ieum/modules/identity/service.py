"""identity 비즈니스 로직.

권한 검사·도메인 규칙·트랜잭션 경계·이벤트 발행이 여기 있다.
SQL 을 직접 쓰지 않고 HTTP 개념(Request/Response)을 참조하지 않는다.
"""

from __future__ import annotations

import io
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pyotp
import qrcode
import qrcode.image.svg
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.crypto import (
    PasswordHashingService,
    SecretBox,
    constant_time_equals,
    hash_token,
)
from ieum.core.exceptions import (
    AuthenticationError,
    ConflictError,
    MFARequiredError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ValidationError,
)
from ieum.core.i18n import available_locales
from ieum.core.ids import new_id, new_token
from ieum.core.logging import get_logger
from ieum.core.outbox import publish
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import (
    PermissionService,
    Scope,
    ScopeKind,
    get_permission_service,
    registry,
)
from ieum.core.time import in_seconds, utcnow
from ieum.modules.identity import audit
from ieum.modules.identity import events as identity_events
from ieum.modules.identity import permissions as perms
from ieum.modules.identity.models import ApiToken, AuditLog, MFACredential, User, UserSession
from ieum.modules.identity.repository import (
    AuditFilter,
    AuditRepository,
    LoginAttemptRepository,
    MFARepository,
    SessionRepository,
    UserRepository,
    normalize_email,
)

log = get_logger(__name__)

BACKUP_CODE_COUNT = 10
#: 사람이 옮겨 적을 코드라 혼동되는 글자(0/O, 1/I/L)를 뺀다.
BACKUP_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BACKUP_CODE_LENGTH = 10
TOTP_VALID_WINDOW = 1  # ±1 스텝 (auth.md 3절)
MFA_SECRET_PURPOSE = "mfa.totp"  # noqa: S105 - HKDF 용도 라벨이지 비밀번호가 아니다


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    session_id: UUID
    mfa_required: bool


@dataclass(frozen=True, slots=True)
class TOTPEnrollment:
    credential_id: UUID
    secret: str
    provisioning_uri: str
    qr_svg: str


def _generate_backup_code() -> str:
    body = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
    return f"{body[:5]}-{body[5:]}"


class AuthService:
    """로그인·세션·리프레시 로테이션."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._mfa = MFARepository(session)
        self._attempts = LoginAttemptRepository(session)
        self._audit = AuditRepository(session)
        self._passwords = PasswordHashingService(
            memory_cost=settings.argon2_memory_cost,
            time_cost=settings.argon2_time_cost,
            parallelism=settings.argon2_parallelism,
        )

    async def login(
        self, *, email: str, password: str, ip: str | None = None, user_agent: str | None = None
    ) -> IssuedTokens:
        """로컬 로그인.

        사용자 열거를 막기 위해 "없는 계정"과 "틀린 비밀번호"의 응답을 구분하지
        않는다. 둘 다 같은 예외·같은 지연으로 끝난다 (auth.md 2절).
        """
        await self._guard_rate_limit(email=email, ip=ip)
        user = await self._users.get_by_email(email)

        if user is None or user.password_hash is None:
            # 계정이 없어도 해시 검증과 동일한 시간을 쓴다. 타이밍으로 존재가 새면 안 된다.
            self._passwords.verify(
                "$argon2id$v=19$m=8,t=1,p=1$c29tZXNhbHQ$aaaaaaaaaaaaaaaaaaaaaa", password
            )
            await self._fail_login(email=email, ip=ip, reason="unknown_user")
            raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않다.")

        if not self._passwords.verify(user.password_hash, password):
            await self._fail_login(email=email, ip=ip, reason="bad_password", user=user)
            raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않다.")

        if user.status == "suspended":
            await self._fail_login(email=email, ip=ip, reason="suspended", user=user)
            raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않다.")

        if user.status == "invited":
            await self._fail_login(email=email, ip=ip, reason="not_activated", user=user)
            raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않다.")

        # 파라미터를 올렸으면 로그인 성공 시점에 조용히 리해시한다.
        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = self._passwords.hash(password)

        self._attempts.record(email=email, ip=ip, succeeded=True)
        user.last_login_at = utcnow()

        mfa_required = await self._mfa.has_any_confirmed(user.id) or user.require_mfa
        tokens = await self._issue_session(
            user, ip=ip, user_agent=user_agent, mfa_satisfied=not mfa_required
        )

        self._audit.record(
            action=audit.LOGIN_SUCCEEDED,
            actor_id=user.id,
            target_type="user",
            target_id=user.id,
            ip=ip,
            metadata={"mfa_required": mfa_required},
        )
        publish(
            self._s,
            identity_events.UserLoggedIn(aggregate_id=user.id, session_id=tokens.session_id, ip=ip),
        )
        return tokens

    async def refresh(
        self, *, refresh_token: str, ip: str | None = None, user_agent: str | None = None
    ) -> IssuedTokens:
        """리프레시 로테이션. 토큰은 1회용이다.

        이미 로테이션된(=사용된) 토큰이 다시 오면 탈취로 간주하고 세션 계열
        전체를 폐기한다. 공격자와 정상 사용자 중 누가 먼저 오든 둘 다 끊긴다 —
        그게 의도다 (auth.md 1절).
        """
        token_hash = hash_token(refresh_token)
        row = await self._sessions.get_by_refresh_hash(token_hash)
        if row is None:
            raise AuthenticationError("리프레시 토큰이 유효하지 않다.")

        if row.rotated_to_id is not None or row.revoked_at is not None:
            revoked = await self._sessions.revoke_family(row.family_id)
            self._audit.record(
                action=audit.REFRESH_REUSE_DETECTED,
                actor_id=row.user_id,
                target_type="session_family",
                target_id=row.family_id,
                ip=ip,
                metadata={"revoked_sessions": revoked},
            )
            publish(
                self._s,
                identity_events.SessionFamilyRevoked(
                    aggregate_id=row.user_id,
                    family_id=row.family_id,
                    reason="refresh_token_reuse",
                ),
            )
            log.warning(
                "auth.refresh_reuse_detected",
                user_id=str(row.user_id),
                family_id=str(row.family_id),
                revoked=revoked,
            )
            raise AuthenticationError("리프레시 토큰이 재사용됐다. 세션을 폐기했다.")

        if row.expires_at <= utcnow():
            raise AuthenticationError("리프레시 토큰이 만료됐다.")

        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("계정을 사용할 수 없다.")

        rotated = await self._issue_session(
            user,
            ip=ip,
            user_agent=user_agent,
            family_id=row.family_id,
            mfa_satisfied_at=row.mfa_satisfied_at,
        )
        row.rotated_to_id = rotated.session_id
        row.revoked_at = utcnow()
        return rotated

    async def logout(self, *, session_id: UUID) -> None:
        row = await self._sessions.get(session_id)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = utcnow()
        self._audit.record(
            action=audit.LOGOUT, actor_id=row.user_id, target_type="session", target_id=row.id
        )

    async def revoke_session(self, *, session_id: UUID, user_id: UUID, actor_id: UUID) -> bool:
        """세션 하나만 끊는다. 못 찾았거나 남의 것이면 False.

        전부 끊기(`revoke_all_sessions`)와 다른 길이 필요한 이유는 기기를 하나만
        잃어버렸을 때다. 전부 끊으면 지금 쓰고 있는 자리에서도 튕겨 나간다.

        **남의 세션인지 여기서 본다.** 라우터에서 보면 서비스를 다시 쓸 때
        빠진다 — 세션 id 만 알면 아무나 끊을 수 있게 된다.
        """
        row = await self._sessions.get(session_id)
        if row is None or row.user_id != user_id or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
        self._audit.record(
            action=audit.SESSION_REVOKED,
            actor_id=actor_id,
            target_type="session",
            target_id=row.id,
            metadata={"user_id": str(user_id)},
        )
        return True

    async def revoke_all_sessions(self, *, user_id: UUID, actor_id: UUID | None = None) -> int:
        count = await self._sessions.revoke_all_for_user(user_id)
        self._audit.record(
            action=audit.SESSION_REVOKED_ALL,
            actor_id=actor_id or user_id,
            target_type="user",
            target_id=user_id,
            metadata={"count": count},
        )
        return count

    async def authenticate_access_token(
        self, access_token: str, *, require_mfa: bool = True
    ) -> tuple[Actor, UserSession]:
        """액세스 토큰으로 액터를 복원한다.

        매 요청 DB 를 한 번 친다. 그 대가로 세션 폐기가 즉시 반영된다 —
        JWT 를 세션으로 쓰지 않는 이유가 이것이다.
        """
        row = await self._sessions.get_by_access_hash(hash_token(access_token))
        if row is None:
            raise AuthenticationError("액세스 토큰이 유효하지 않다.")

        now = utcnow()
        if row.revoked_at is not None:
            raise AuthenticationError("세션이 폐기됐다.")
        if row.access_expires_at <= now:
            raise AuthenticationError("액세스 토큰이 만료됐다.", code="auth.access_token_expired")

        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("계정을 사용할 수 없다.")

        # MFA 미완료 세션은 등록·검증 API 외 모든 요청이 막힌다 (auth.md 3절).
        if require_mfa and row.mfa_satisfied_at is None:
            raise MFARequiredError()

        actor = Actor(
            user_id=user.id,
            email=user.email,
            is_customer=user.is_customer,
            is_active=user.is_active,
            locale=user.locale,
            timezone=user.timezone,
            session_id=row.id,
            mfa_satisfied_at=row.mfa_satisfied_at,
            group_ids=await self._users.group_ids_for(user.id),
        )
        return actor, row

    async def _issue_session(
        self,
        user: User,
        *,
        ip: str | None,
        user_agent: str | None,
        family_id: UUID | None = None,
        mfa_satisfied: bool = True,
        mfa_satisfied_at: datetime | None = None,
    ) -> IssuedTokens:
        access_token = new_token()
        refresh_token = new_token()
        session_id = new_id()

        satisfied_at = mfa_satisfied_at or (utcnow() if mfa_satisfied else None)
        row = UserSession(
            id=session_id,
            user_id=user.id,
            refresh_token_hash=hash_token(refresh_token),
            access_token_hash=hash_token(access_token),
            access_expires_at=in_seconds(self._settings.access_token_ttl_seconds),
            family_id=family_id or session_id,
            ip=ip,
            user_agent=user_agent,
            expires_at=in_seconds(self._settings.refresh_token_ttl_seconds),
            mfa_satisfied_at=satisfied_at,
        )
        self._sessions.add(row)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_ttl_seconds,
            session_id=session_id,
            mfa_required=satisfied_at is None,
        )

    async def _guard_rate_limit(self, *, email: str, ip: str | None) -> None:
        failures = await self._attempts.recent_failures(
            email=email, ip=ip, window_seconds=self._settings.login_attempt_window_seconds
        )
        if failures >= self._settings.login_max_attempts:
            # 잠그지 않고 지연시킨다. 잠그면 타인이 남의 계정을 잠글 수 있다.
            raise RateLimitedError(
                retry_after_seconds=min(
                    2 ** (failures - self._settings.login_max_attempts + 1), 300
                )
            )

    async def _fail_login(
        self, *, email: str, ip: str | None, reason: str, user: User | None = None
    ) -> None:
        self._attempts.record(email=email, ip=ip, succeeded=False)
        self._audit.record(
            action=audit.LOGIN_FAILED,
            actor_id=user.id if user else None,
            target_type="user",
            target_id=user.id if user else None,
            ip=ip,
            metadata={"reason": reason, "email": normalize_email(email)},
        )


class MFAService:
    """TOTP 와 백업 코드 (auth.md 3절)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._mfa = MFARepository(session)
        self._sessions = SessionRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)
        self._box = SecretBox(settings.secret_key.get_secret_value(), purpose=MFA_SECRET_PURPOSE)

    async def start_totp_enrollment(
        self, *, user_id: UUID, mfa_satisfied: bool, label: str | None = None
    ) -> TOTPEnrollment:
        """등록 시작. 확인 코드를 넣기 전까지 활성화되지 않는다.

        MFA 미완료 세션(=비밀번호만 통과한 상태)에서 등록을 허용하는 것은
        **아직 MFA 가 없는 사용자의 강제 등록 흐름을 위해서만**이다.
        이미 MFA 가 있는 사용자에게까지 열어두면, 비밀번호만 아는 공격자가
        자기 인증기를 새로 등록해 2FA 를 그대로 우회할 수 있다.
        """
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없다.")

        if not mfa_satisfied and await self._mfa.has_any_confirmed(user_id):
            raise MFARequiredError(
                "이미 등록된 2FA 가 있다. 먼저 인증해야 새 인증기를 등록할 수 있다."
            )

        secret = pyotp.random_base32()
        credential = MFACredential(
            user_id=user_id,
            kind="totp",
            secret_enc=self._box.encrypt(secret),
            label=label or "Authenticator",
        )
        self._mfa.add(credential)
        await self._s.flush()

        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Ieum")
        return TOTPEnrollment(
            credential_id=credential.id,
            secret=secret,
            provisioning_uri=uri,
            qr_svg=_qr_svg(uri),
        )

    async def confirm_totp_enrollment(
        self, *, user_id: UUID, credential_id: UUID, code: str, mfa_satisfied: bool
    ) -> None:
        credential = await self._mfa.get(credential_id)
        if credential is None or credential.user_id != user_id or credential.kind != "totp":
            raise NotFoundError("등록 중인 TOTP 를 찾을 수 없다.")
        if credential.confirmed_at is not None:
            raise ConflictError("이미 확인된 자격증명이다.")

        # start_totp_enrollment 와 같은 이유로 여기서도 막는다.
        if not mfa_satisfied and await self._mfa.has_any_confirmed(user_id):
            raise MFARequiredError("이미 등록된 2FA 가 있다. 먼저 인증해야 한다.")

        totp = pyotp.TOTP(self._box.decrypt(credential.secret_enc))
        matched = self._matching_timestep(totp, code)
        if matched is None:
            raise ValidationError("확인 코드가 올바르지 않다.", code="auth.mfa_invalid_code")

        now = utcnow()
        credential.confirmed_at = now
        credential.last_used_at = now
        credential.last_timestep = matched

        self._audit.record(
            action=audit.MFA_ENROLLED,
            actor_id=user_id,
            target_type="mfa_credential",
            target_id=credential.id,
            metadata={"kind": "totp"},
        )
        publish(self._s, identity_events.MFAEnrolled(aggregate_id=user_id, kind="totp"))

    async def verify(self, *, user_id: UUID, session_id: UUID, code: str) -> None:
        """로그인 후 2FA 확인. TOTP 우선, 실패하면 백업 코드로 시도한다."""
        row = await self._sessions.get(session_id)
        if row is None or row.user_id != user_id or row.revoked_at is not None:
            raise AuthenticationError("세션이 유효하지 않다.")

        if await self._verify_totp(user_id, code) or await self._consume_backup_code(user_id, code):
            row.mfa_satisfied_at = utcnow()
            self._audit.record(
                action=audit.MFA_VERIFIED,
                actor_id=user_id,
                target_type="session",
                target_id=row.id,
            )
            return

        self._audit.record(
            action=audit.MFA_FAILED, actor_id=user_id, target_type="session", target_id=row.id
        )
        raise ValidationError("인증 코드가 올바르지 않다.", code="auth.mfa_invalid_code")

    async def issue_backup_codes(self, *, user_id: UUID, mfa_satisfied: bool) -> list[str]:
        """10개를 발급한다. 재발급하면 기존 코드는 전량 무효화된다.

        MFA 를 통과한 세션에서만 발급한다. 미완료 세션에 열어두면 비밀번호만
        아는 공격자가 새 백업 코드를 받아 그대로 2FA 를 우회한다.
        """
        if not mfa_satisfied:
            raise MFARequiredError("백업 코드는 2FA 를 통과한 뒤에만 발급할 수 있다.")
        await self._mfa.delete_backup_codes(user_id)
        codes = [_generate_backup_code() for _ in range(BACKUP_CODE_COUNT)]
        for code in codes:
            self._mfa.add(
                MFACredential(
                    user_id=user_id,
                    kind="backup_code",
                    secret_enc=hash_token(code),  # 해시만 저장한다. 복원 불가.
                    confirmed_at=utcnow(),
                )
            )
        self._audit.record(
            action=audit.MFA_BACKUP_CODES_ISSUED,
            actor_id=user_id,
            target_type="user",
            target_id=user_id,
            metadata={"count": len(codes)},
        )
        return codes

    async def _verify_totp(self, user_id: UUID, code: str) -> bool:
        credential = await self._mfa.confirmed_totp_for(user_id)
        if credential is None:
            return False

        matched = self._matching_timestep(
            pyotp.TOTP(self._box.decrypt(credential.secret_enc)), code
        )
        if matched is None:
            return False

        # 재사용 방지: 이미 쓴 스텝 이하의 코드는 받지 않는다.
        # 시계 기준이 아니라 **실제로 맞은 코드의 스텝**을 기준으로 판단한다.
        # 시계로 판단하면 ±1 스텝 허용 범위 안에서 같은 코드가 두 번 통과한다.
        if credential.last_timestep is not None and matched <= credential.last_timestep:
            return False

        credential.last_timestep = matched
        credential.last_used_at = utcnow()
        return True

    @staticmethod
    def _matching_timestep(totp: pyotp.TOTP, code: str) -> int | None:
        """코드가 맞는 타임스텝을 찾는다. 허용 범위(±1) 안에서만 본다."""
        current = int(utcnow().timestamp()) // 30
        for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
            step = current + offset
            if totp.verify(code, for_time=datetime.fromtimestamp(step * 30, UTC), valid_window=0):
                return step
        return None

    async def _consume_backup_code(self, user_id: UUID, code: str) -> bool:
        candidate = hash_token(code.strip().upper())
        for stored in await self._mfa.unused_backup_codes_for(user_id):
            if constant_time_equals(stored.secret_enc, candidate):
                stored.used_at = utcnow()
                stored.last_used_at = stored.used_at
                return True
        return False


class UserService:
    """사용자 초대·활성화·비밀번호."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._audit = AuditRepository(session)
        self._passwords = PasswordHashingService(
            memory_cost=settings.argon2_memory_cost,
            time_cost=settings.argon2_time_cost,
            parallelism=settings.argon2_parallelism,
        )

    async def invite(
        self,
        *,
        email: str,
        display_name: str,
        locale: str = "en",
        timezone: str = "UTC",
        invited_by: UUID | None = None,
    ) -> User:
        normalized = normalize_email(email)
        if await self._users.email_exists(normalized):
            raise ConflictError("이미 등록된 이메일이다.", code="identity.email_taken")

        user = User(
            email=normalized,
            display_name=display_name,
            locale=locale,
            timezone=timezone,
            status="invited",
        )
        self._users.add(user)
        await self._s.flush()

        self._audit.record(
            action=audit.USER_INVITED,
            actor_id=invited_by,
            target_type="user",
            target_id=user.id,
            metadata={"email": normalized},
        )
        publish(
            self._s,
            identity_events.UserInvited(
                aggregate_id=user.id, email=normalized, invited_by=invited_by
            ),
        )
        return user

    async def directory(
        self,
        request: PageRequest,
        *,
        query: str | None = None,
        ids: Sequence[UUID] | None = None,
    ) -> Page[User]:
        """담당자·멘션 피커가 쓰는 사용자 목록.

        `ids` 를 주면 그 사용자들만 돌려준다 — 목록 화면이 이미 알고 있는
        담당자 id 를 이름으로 바꿀 때 쓴다. 전체를 훑지 않아도 된다.
        """
        return await self._users.list_page(request, query=query, ids=ids)

    async def set_password(self, *, user_id: UUID, new_password: str) -> None:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없다.")
        self._validate_password(new_password)
        user.password_hash = self._passwords.hash(new_password)

    async def activate_with_password(self, *, user_id: UUID, password: str) -> User:
        """초대 수락. 비밀번호를 설정하고 계정을 활성화한다."""
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없다.")
        if user.status == "suspended":
            raise ConflictError("정지된 계정이다.")

        self._validate_password(password)
        user.password_hash = self._passwords.hash(password)
        user.status = "active"

        self._audit.record(
            action=audit.USER_ACTIVATED,
            actor_id=user.id,
            target_type="user",
            target_id=user.id,
        )
        publish(self._s, identity_events.UserActivated(aggregate_id=user.id, email=user.email))
        return user

    async def update_profile(self, *, user_id: UUID, locale: str | None = None) -> User:
        """본인이 자기 설정을 바꾼다.

        언어는 **서버에 남아야** 한다. 브라우저에만 두면 다른 기기에서 다시
        영어로 열리고, 무엇보다 알림 메일이 수신자 언어로 안 나간다 — 서버는
        `user.locale` 로 렌더한다(i18n.md 3절).
        """
        user = await self._users.get(user_id)
        if user is None:  # pragma: no cover - 액터가 있으면 사용자도 있다
            raise NotFoundError("사용자를 찾을 수 없다.")

        if locale is not None and locale != user.locale:
            supported = available_locales(str(self._settings.i18n_catalog_dir))
            if locale not in supported:
                raise ValidationError(
                    f"지원하지 않는 언어다: {locale}",
                    code="identity.unsupported_locale",
                    details={"supported": list(supported)},
                )
            user.locale = locale
            self._audit.record(
                action=audit.USER_LOCALE_CHANGED,
                actor_id=user_id,
                target_type="user",
                target_id=user_id,
                metadata={"locale": locale},
            )
        return user

    async def change_password(
        self, *, user_id: UUID, current_password: str, new_password: str
    ) -> None:
        user = await self._users.get(user_id)
        if user is None or user.password_hash is None:
            raise NotFoundError("사용자를 찾을 수 없다.")
        if not self._passwords.verify(user.password_hash, current_password):
            raise AuthenticationError("현재 비밀번호가 올바르지 않다.")

        self._validate_password(new_password)
        user.password_hash = self._passwords.hash(new_password)

        # 비밀번호가 바뀌면 다른 기기의 세션을 끊는다. 탈취 대응의 핵심이다.
        revoked = await self._sessions.revoke_all_for_user(user_id)
        self._audit.record(
            action=audit.USER_PASSWORD_CHANGED,
            actor_id=user_id,
            target_type="user",
            target_id=user_id,
            metadata={"revoked_sessions": revoked},
        )

    def _validate_password(self, password: str) -> None:
        minimum = self._settings.password_min_length
        if len(password) < minimum:
            raise ValidationError(
                f"비밀번호는 {minimum}자 이상이어야 한다.",
                code="identity.password_too_short",
                details={"min_length": minimum},
            )


def _qr_svg(uri: str) -> str:
    """otpauth URI 를 SVG QR 로. 인라인 이미지로 바로 쓸 수 있게 문자열로 준다."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


__all__ = [
    "AuthService",
    "IssuedTokens",
    "MFAService",
    "TOTPEnrollment",
    "UserService",
]


class AuditService:
    """감사 로그 조회. 쓰기는 각 서비스가 `AuditRepository` 로 직접 한다.

    읽기만 여기 모으는 이유는 **권한** 때문이다. 감사 로그는 누가 언제 무엇을
    했는지가 통째로 들어 있어, 목록 하나가 조직의 활동 전부를 드러낸다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._audit = AuditRepository(session)
        self._users = UserRepository(session)

    async def list(
        self, actor: Actor, filters: AuditFilter, request: PageRequest
    ) -> tuple[Page[AuditLog], dict[UUID, str]]:
        """페이지와, 그 안에 나온 행위자의 이메일.

        이메일을 함께 주는 이유는 화면이 id 만 받으면 사람을 못 알아보기
        때문이다. 목록마다 사용자 API 를 다시 부르게 하면 N+1 이 화면 쪽으로
        옮겨 갈 뿐이다.
        """
        await self._require_view(actor)
        page = await self._audit.list(filters, request)
        rows = await self._users.get_many([r.actor_id for r in page.items if r.actor_id])
        return page, {u.id: u.email for u in rows}

    async def export(self, actor: Actor, filters: AuditFilter) -> AsyncIterator[bytes]:
        """CSV 스트림. 권한은 **스트림을 만들기 전에** 본다.

        제너레이터 안에서 보면 응답 헤더가 이미 200 으로 나간 뒤라, 거절이
        빈 파일로 보인다.
        """
        await self._require_view(actor)
        return audit.stream_csv(self._s, filters)

    async def _require_view(self, actor: Actor) -> None:
        await self._perms.require(self._s, actor, perms.AUDIT_VIEW, scope=Scope.global_())


class ApiTokenService:
    """개인 액세스 토큰(PAT).

    토큰은 **발급 시 한 번만** 평문으로 보여준다. 해시만 저장하므로 잃어버리면
    다시 만들어야 한다 — 조회로 다시 볼 수 있으면 DB 유출이 곧 계정 탈취다.

    스코프는 발급 시 고른 권한의 교집합으로 동작한다. 사용자 권한이 나중에
    늘어나도 토큰이 같이 커지지 않는다.
    """

    #: 토큰 접두사. 시크릿 스캐너가 커밋에서 잡을 수 있고, 인증 경로가
    #: DB 를 치기 전에 어떤 종류인지 구분할 수 있다.
    PREFIX = "ieum_pat_"
    MAX_PER_USER = 20
    #: last_used_at 을 매 요청 쓰면 토큰 하나가 뜨거운 로우가 된다.
    TOUCH_INTERVAL_SECONDS = 300

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)

    @staticmethod
    def looks_like_token(raw: str) -> bool:
        return raw.startswith(ApiTokenService.PREFIX)

    async def issue(
        self,
        actor: Actor,
        *,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int | None = None,
    ) -> tuple[ApiToken, str]:
        """토큰을 만들고 (행, 평문) 을 돌려준다."""
        label = name.strip()
        if not label:
            raise ValidationError("토큰 이름이 필요하다.", code="identity.token_name_required")

        # PAT 은 오래 사는 무기명 자격증명이다. 2차 요소 없이 발급하면
        # 비밀번호 하나가 새는 순간 만료 없는 접근 권한이 따라 나간다.
        # step-up(최근 5분 내 MFA)만으로는 부족하다 — MFA 를 아예 등록하지
        # 않은 계정은 로그인 자체가 그 창을 채우기 때문이다.
        if await MFARepository(self._s).confirmed_totp_for(actor.user_id) is None:
            raise PermissionDeniedError(
                "API 토큰을 발급하려면 2단계 인증을 먼저 등록해야 한다.",
                code="identity.token_requires_mfa",
            )

        requested = _validate_scopes(scopes)
        # 자기가 가진 것보다 넓은 스코프는 줄 수 없다. 검사를 생략하면
        # 권한이 늘어난 뒤 그 토큰이 조용히 강해진다.
        await self._require_owned_scopes(actor, requested)

        existing = await self._count_active(actor.user_id)
        if existing >= self.MAX_PER_USER:
            raise ConflictError(
                "토큰이 너무 많다. 쓰지 않는 것을 먼저 지운다.",
                code="identity.too_many_tokens",
                details={"max": self.MAX_PER_USER},
            )

        raw = f"{self.PREFIX}{secrets.token_urlsafe(32)}"
        row = ApiToken(
            user_id=actor.user_id,
            name=label,
            token_hash=hash_token(raw),
            scopes=sorted(requested),
            expires_at=(None if expires_in_days is None else in_seconds(expires_in_days * 86_400)),
        )
        self._s.add(row)
        await self._s.flush()

        self._audit.record(
            action=audit.TOKEN_ISSUED,
            actor_id=actor.user_id,
            target_type="api_token",
            target_id=row.id,
            metadata={"name": label, "scopes": sorted(requested)},
        )
        log.info("api_token.issued", token=str(row.id), user=str(actor.user_id))
        return row, raw

    async def list_for(self, actor: Actor) -> list[ApiToken]:
        stmt = (
            select(ApiToken)
            .where(ApiToken.user_id == actor.user_id)
            .where(ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def revoke(self, actor: Actor, token_id: UUID) -> None:
        row = await self._s.get(ApiToken, token_id)
        # 남의 토큰은 존재 자체를 숨긴다.
        if row is None or row.user_id != actor.user_id or row.revoked_at is not None:
            raise NotFoundError("토큰을 찾을 수 없다.")
        row.revoked_at = utcnow()
        self._audit.record(
            action=audit.TOKEN_REVOKED,
            actor_id=actor.user_id,
            target_type="api_token",
            target_id=row.id,
            metadata={"name": row.name},
        )
        await self._s.flush()

    async def authenticate(self, raw: str) -> Actor:
        """PAT 으로 액터를 복원한다.

        세션과 달리 MFA 상태를 갖지 않는다. 대신 step-up 이 필요한 권한은
        PermissionService 가 토큰 액터에게 거절한다 — 오래 사는 토큰은
        "방금 사람이 MFA 를 통과했다" 를 증명할 수 없다.
        """
        stmt = select(ApiToken).where(ApiToken.token_hash == hash_token(raw))
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise AuthenticationError("API 토큰이 유효하지 않다.", code="auth.invalid_token")

        now = utcnow()
        if row.revoked_at is not None:
            raise AuthenticationError("API 토큰이 폐기됐다.", code="auth.invalid_token")
        if row.expires_at is not None and row.expires_at <= now:
            raise AuthenticationError("API 토큰이 만료됐다.", code="auth.token_expired")

        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("계정을 사용할 수 없다.")

        # 매 요청 쓰면 뜨거운 로우가 된다. 간격을 두고만 갱신한다.
        if (
            row.last_used_at is None
            or (now - row.last_used_at).total_seconds() > self.TOUCH_INTERVAL_SECONDS
        ):
            row.last_used_at = now

        return Actor(
            user_id=user.id,
            email=user.email,
            is_customer=user.is_customer,
            is_active=user.is_active,
            locale=user.locale,
            timezone=user.timezone,
            # 사람 세션이 아니므로 session_id 는 없다.
            mfa_satisfied_at=None,
            group_ids=await self._users.group_ids_for(user.id),
            api_token_id=row.id,
            token_scopes=frozenset(row.scopes),
        )

    async def _count_active(self, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ApiToken)
            .where(ApiToken.user_id == user_id)
            .where(ApiToken.revoked_at.is_(None))
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def _require_owned_scopes(self, actor: Actor, requested: set[str]) -> None:
        service = get_permission_service()
        missing: list[str] = []
        for permission in sorted(requested):
            definition = registry.get(permission)
            # 전역 권한만 여기서 판정한다. 프로젝트 스코프 권한은 프로젝트
            # 어딘가에 있으면 통과 — 토큰 사용 시점에 스코프별로 다시 본다.
            if ScopeKind.GLOBAL in definition.scope_kinds and not await service.has(
                self._s, actor, permission, scope=Scope.global_()
            ):
                missing.append(permission)
        if missing:
            raise PermissionDeniedError(
                "자기가 갖지 않은 권한은 토큰에 담을 수 없다.",
                details={"permissions": missing},
            )


def _validate_scopes(scopes: Sequence[str]) -> set[str]:
    if not scopes:
        raise ValidationError("스코프를 하나 이상 고른다.", code="identity.token_scopes_required")
    unknown = [s for s in scopes if s not in registry]
    if unknown:
        # 오타 난 스코프를 통과시키면 토큰이 "아무것도 못 하는" 상태로
        # 조용히 만들어진다.
        raise ValidationError(
            "알 수 없는 권한이다.",
            code="identity.unknown_scope",
            details={"scopes": sorted(unknown)},
        )
    return set(scopes)
