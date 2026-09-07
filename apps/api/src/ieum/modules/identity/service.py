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
from functools import lru_cache
from uuid import UUID

import pyotp
import qrcode
import qrcode.image.svg
from jwt import PyJWKClient
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
    MFAEnrollmentRequiredError,
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
from ieum.modules.identity import audit, oidc, saml
from ieum.modules.identity import events as identity_events
from ieum.modules.identity import permissions as perms
from ieum.modules.identity.models import (
    ApiToken,
    AuditLog,
    IdentityProvider,
    MFACredential,
    SamlFlow,
    User,
    UserGroup,
    UserSession,
)
from ieum.modules.identity.repository import (
    AuditFilter,
    AuditRepository,
    GroupRepository,
    IdentityProviderRepository,
    LoginAttemptRepository,
    MFARepository,
    SamlRepository,
    SessionRepository,
    UserRepository,
    normalize_email,
)
from ieum.modules.identity.sso import Claims
from ieum.modules.org import contracts as org

log = get_logger(__name__)


@lru_cache(maxsize=16)
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    """JWKS 를 캐시한다. 로그인마다 IdP 를 치면 느리고, IdP 가 막는다.

    `PyJWKClient` 가 캐시와 **키 회전**을 함께 맡는다 — 모르는 `kid` 가 오면
    한 번 다시 받는다. 회전 직후 로그인이 실패하지 않게 하는 자리다.
    """
    return PyJWKClient(jwks_uri, cache_keys=True, lifespan=600)


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
    #: 강제인데 자격증명이 하나도 없다. 화면은 확인이 아니라 **등록**으로 간다.
    mfa_enrollment_required: bool = False


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

        enrolled = await self._mfa.has_any_confirmed(user.id)
        # 개인 플래그는 identity 것, 조직·역할 정책은 org 것이다(모듈 경계).
        enforced = user.require_mfa or await org.mfa_required_for(
            self._s, user_id=user.id, group_ids=await self._users.group_ids_for(user.id)
        )
        mfa_required = enrolled or enforced
        tokens = await self._issue_session(
            user,
            ip=ip,
            user_agent=user_agent,
            mfa_satisfied=not mfa_required,
            # 켜라고는 하는데 켤 것이 없다. 확인 화면으로 보내면 만들 수 없는
            # 코드를 요구받아 계정이 잠긴다.
            enrollment_required=enforced and not enrolled,
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
            # **미완료 상태를 그대로 물려준다.** 이 한 줄이 없으면
            # `_issue_session` 의 기본값(True)이 로테이션마다 완료 상태를 새로
            # 만들어 준다 — 비밀번호만 통과한 세션이 `/auth/refresh` 한 번으로
            # 열리고, 2FA 는 장식이 된다.
            mfa_satisfied=row.mfa_satisfied_at is not None,
            # 통과했다는 사실도 함께 넘긴다. 안 넘기면 토큰이 돌 때마다
            # 인증기를 다시 꺼내야 한다.
            mfa_verified=row.mfa_verified,
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

    async def issue_for_sso(
        self,
        user: User,
        *,
        ip: str | None,
        user_agent: str | None,
        idp_verified_mfa: bool,
    ) -> IssuedTokens:
        """SSO 로 들어온 사람의 세션.

        우리 쪽 2FA 는 별도다. IdP 에 위임하는 정책이면 그쪽이 실제로 2차
        요소를 요구했는지(`amr`/`acr`)까지 확인한 결과를 받는다 — 위임을
        켰다는 사실만으로 통과시키면 비밀번호 하나로 민감 작업이 열린다.
        """
        enrolled = await self._mfa.has_any_confirmed(user.id)
        enforced = user.require_mfa or await org.mfa_required_for(
            self._s, user_id=user.id, group_ids=await self._users.group_ids_for(user.id)
        )
        satisfied = idp_verified_mfa or not (enrolled or enforced)
        return await self._issue_session(
            user,
            ip=ip,
            user_agent=user_agent,
            mfa_satisfied=satisfied,
            enrollment_required=enforced and not enrolled and not idp_verified_mfa,
            # IdP 가 2차 요소를 실제로 요구했으면 그게 증명이다.
            mfa_verified=idp_verified_mfa,
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
            # 등록할 것이 있느냐로 갈라 던진다. 뭉뚱그리면 아직 등록도 안 한
            # 사람에게 "코드를 넣으라" 는 화면이 뜨고 계정이 잠긴다.
            if await self._mfa.has_any_confirmed(user.id):
                raise MFARequiredError()
            raise MFAEnrollmentRequiredError()

        actor = Actor(
            user_id=user.id,
            email=user.email,
            is_customer=user.is_customer,
            is_active=user.is_active,
            locale=user.locale,
            timezone=user.timezone,
            session_id=row.id,
            mfa_satisfied_at=row.mfa_satisfied_at,
            mfa_verified=row.mfa_verified,
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
        enrollment_required: bool = False,
        mfa_verified: bool = False,
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
            # 로그인만으로는 증명이 아니다. `satisfied_at` 은 MFA 가 필요 없는
            # 계정에도 채워지므로, step-up 이 그걸 보면 2FA 없는 관리자가
            # 민감 작업을 전부 통과한다.
            mfa_verified=mfa_verified,
        )
        self._sessions.add(row)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_ttl_seconds,
            session_id=session_id,
            mfa_required=satisfied_at is None,
            mfa_enrollment_required=enrollment_required,
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
        self,
        *,
        user_id: UUID,
        credential_id: UUID,
        code: str,
        mfa_satisfied: bool,
        session_id: UUID | None = None,
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

        # 방금 맞힌 코드가 곧 소지 증명이다. 이 세션을 여기서 만족시키지
        # 않으면 등록을 마치자마자 같은 인증기의 코드를 또 넣으라고 한다.
        #
        # `mfa_satisfied` 여부와 무관하게 표시한다. 2FA 가 없던 계정의 세션은
        # 이미 "만족" 으로 채워져 있지만 **증명한 적은 없다** — 그 상태에서
        # 등록만 하고 증명 표시를 안 남기면, 방금 인증기를 등록한 사람이
        # step-up 작업을 계속 거절당한다.
        if session_id is not None:
            row = await SessionRepository(self._s).get(session_id)
            if row is not None and row.revoked_at is None:
                row.mfa_satisfied_at = now
                row.mfa_verified = True

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
            row.mfa_verified = True
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


class SsoService:
    """OIDC 로그인 (auth.md 4절).

    검증은 `oidc` 모듈이 한다. 여기서는 **검증을 통과한 뒤** 무엇을 하느냐를
    정한다: 누구인지 찾고, 없으면 만들고, 그룹을 맞추고, 세션을 연다.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._providers = IdentityProviderRepository(session)
        self._users = UserRepository(session)
        self._groups = GroupRepository(session)
        self._audit = AuditRepository(session)
        self._auth = AuthService(session, settings)

    async def providers(self) -> list[IdentityProvider]:
        return await self._providers.enabled()

    async def provider_for_email(self, email: str) -> IdentityProvider | None:
        """이메일 도메인으로 IdP 를 고른다. 없으면 로컬 로그인이다."""
        domain = oidc.domain_of(email)
        for provider in await self._providers.enabled():
            if domain in {str(d).lower() for d in provider.email_domains}:
                return provider
        return None

    #: 콜백이 돌아올 자리. 화면 경로는 서버가 정한다.
    CALLBACK_PATH = "/auth/callback"

    def redirect_uri(self) -> str:
        """**서버가 만든다.** 클라이언트가 준 값을 그대로 쓰면, 공격자가 자기
        주소를 넣고 흐름을 시작해 인가 코드를 가져갈 수 있다. IdP 도 등록된
        주소만 받지만, 느슨하게 맞추는 IdP 가 있어 여기서도 막는다.
        """
        return f"{self._settings.base_url.rstrip('/')}{self.CALLBACK_PATH}"

    async def begin(self, provider_id: UUID) -> str:
        """인가 URL. 전이 상태는 `state` 에 봉해 보낸다 — 서버는 기억하지 않는다."""
        provider = await self._require_provider(provider_id)
        redirect_uri = self.redirect_uri()
        verifier = oidc.new_verifier()
        flow = oidc.Flow(
            provider_id=provider.id,
            nonce=secrets.token_urlsafe(24),
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        client_id, _, _, _ = self._oidc_config(provider)
        return oidc.authorization_url(
            endpoint=provider.authorization_endpoint,
            client_id=client_id,
            scopes=provider.scopes,
            redirect_uri=redirect_uri,
            state=oidc.seal_state(flow, self._settings),
            nonce=flow.nonce,
            code_challenge=oidc.challenge_for(verifier),
        )

    async def complete(
        self, *, code: str, state: str, ip: str | None = None, user_agent: str | None = None
    ) -> IssuedTokens:
        """콜백. 여기를 통과하면 우리 세션이 열린다."""
        flow = oidc.open_state(state, self._settings)
        provider = await self._require_provider(flow.provider_id)

        client_id, secret_enc, token_endpoint, jwks_uri = self._oidc_config(provider)
        tokens = await oidc.exchange_code(
            token_endpoint=token_endpoint,
            client_id=client_id,
            client_secret=self._secret_box().decrypt(secret_enc),
            code=code,
            redirect_uri=flow.redirect_uri,
            code_verifier=flow.code_verifier,
        )
        raw = oidc.verify_id_token(
            tokens["id_token"],
            jwks_client=_jwks_client(jwks_uri),
            issuer=provider.issuer,
            client_id=client_id,
            nonce=flow.nonce,
        )
        claims = oidc.read_claims(
            raw,
            email_claim=provider.email_claim,
            name_claim=provider.name_claim,
            groups_claim=provider.groups_claim,
            trust_idp_mfa=provider.trust_idp_mfa,
        )
        user = await self.accept(provider, claims, ip=ip)
        return await self.open_session(
            user, ip=ip, user_agent=user_agent, idp_verified_mfa=claims.mfa_satisfied
        )

    async def accept(
        self, provider: IdentityProvider, claims: Claims, *, ip: str | None = None
    ) -> User:
        """검증을 통과한 클레임을 받아들인다. **OIDC 와 SAML 이 여기서 만난다.**

        프로토콜마다 검증하는 방식은 다르지만, 통과한 뒤에 하는 일은 하나다 —
        누구인지 찾고, 없으면 만들고, 그룹을 맞춘다. 두 벌로 두면 한쪽만
        고치는 날이 오고, 그때 고쳐지지 않은 쪽이 구멍이 된다.

        세션은 여기서 열지 않는다. SAML 은 ACS(브라우저 POST)와 화면이 갈려
        있어, 받아들이는 시점과 세션을 여는 시점이 다르다.
        """
        user = await self._resolve_user(provider, claims)
        await self._sync_groups(user, claims.groups)
        user.last_login_at = utcnow()
        self._audit.record(
            action=audit.SSO_LOGIN_SUCCEEDED,
            actor_id=user.id,
            target_type="user",
            target_id=user.id,
            ip=ip,
            metadata={
                "provider": provider.name,
                "kind": provider.kind,
                "idp_mfa": claims.mfa_satisfied,
            },
        )
        return user

    async def open_session(
        self,
        user: User,
        *,
        ip: str | None,
        user_agent: str | None,
        idp_verified_mfa: bool,
    ) -> IssuedTokens:
        return await self._auth.issue_for_sso(
            user, ip=ip, user_agent=user_agent, idp_verified_mfa=idp_verified_mfa
        )

    async def _resolve_user(self, provider: IdentityProvider, claims: Claims) -> User:
        """`sub` 로 찾는다. 이메일은 **처음 잇는 순간**에만 쓴다.

        `sub` 는 IdP 안에서 바뀌지 않는 값이다. 이메일로 매번 찾으면, 주소를
        바꾼 사람이 자기 계정을 잃거나 남의 계정에 들어간다.
        """
        linked = await self._providers.identity(provider.id, claims.subject)
        if linked is not None:
            linked.last_login_at = utcnow()
            user = await self._users.get(linked.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("계정을 사용할 수 없다.")
            return user

        if claims.email and provider.link_verified_email:
            # **검증된 이메일만** 이어 준다(claims 단계에서 이미 걸러진다).
            existing = await self._users.get_by_email(claims.email)
            if existing is not None:
                self._providers.link(provider.id, existing.id, claims.subject)
                self._audit.record(
                    action=audit.SSO_ACCOUNT_LINKED,
                    actor_id=existing.id,
                    target_type="user",
                    target_id=existing.id,
                    metadata={"provider": provider.name},
                )
                return existing

        if not provider.jit_provisioning:
            raise AuthenticationError(
                "이 조직에 계정이 없다. 관리자에게 초대를 요청해 달라.",
                code="auth.sso_no_account",
            )
        if not claims.email:
            # 이메일 없이 계정을 만들면 메일도 멘션도 안 되는 유령이 남는다.
            raise AuthenticationError(
                "IdP 가 검증된 이메일을 주지 않았다.", code="auth.sso_email_required"
            )
        return await self._provision(provider, claims)

    async def _provision(self, provider: IdentityProvider, claims: Claims) -> User:
        assert claims.email is not None
        user = User(
            email=normalize_email(claims.email),
            display_name=claims.name or claims.email.split("@")[0],
            # 비밀번호가 없다. 로컬 로그인 경로는 `password_hash is None` 에서 막힌다.
            password_hash=None,
            status="active",
        )
        self._users.add(user)
        await self._s.flush()
        self._providers.link(provider.id, user.id, claims.subject)
        self._audit.record(
            action=audit.SSO_USER_PROVISIONED,
            actor_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"provider": provider.name},
        )
        log.info("auth.sso_provisioned", user=str(user.id), provider=provider.name)
        return user

    async def _sync_groups(self, user: User, names: list[str]) -> None:
        """IdP 가 준 그룹에 맞춘다. **뺀 것도 뺀다.**

        더하기만 하면 권한 회수가 안 된다 — 팀을 옮긴 사람이 옛 팀 자료를
        계속 본다. 손으로 만든 그룹(`source='local'`)은 건드리지 않는다.
        """
        wanted: set[UUID] = set()
        for name in names:
            group = await self._groups.get_by_name(name)
            if group is None:
                group = self._groups.add(UserGroup(name=name, source="idp"))
                await self._s.flush()
            elif group.source != "idp":
                # 같은 이름의 로컬 그룹이 있으면 IdP 가 가져가지 않는다.
                continue
            wanted.add(group.id)
            await self._groups.add_member(group.id, user.id)

        current = await self._providers.idp_group_ids_for(user.id)
        await self._providers.drop_members(user.id, sorted(current - wanted))

    async def _require_provider(self, provider_id: UUID) -> IdentityProvider:
        provider = await self._providers.get(provider_id)
        if provider is None or not provider.is_enabled:
            raise NotFoundError("그 IdP 를 찾을 수 없다.")
        if provider.kind != "oidc":
            # SAML 은 다른 경로(`SamlService`)를 쓴다. 여기로 오면 설정이
            # 잘못 연결된 것이다 — 반쯤 진행하고 터지는 것보다 낫다.
            raise NotFoundError("그 IdP 는 OIDC 가 아니다.")
        return provider

    @staticmethod
    def _oidc_config(provider: IdentityProvider) -> tuple[str, str, str, str]:
        """(client_id, client_secret_enc, token_endpoint, jwks_uri).

        DB 의 CHECK 가 `kind='oidc'` 행에 이 넷을 요구한다. 그래도 여기서 한 번
        더 본다 — 제약을 지나온 행만 온다는 보장을 코드가 스스로 들고 있어야
        타입도 서고, 마이그레이션 사고가 나도 로그인 도중에 터지지 않는다.
        """
        client_id = provider.client_id
        secret = provider.client_secret_enc
        token_endpoint = provider.token_endpoint
        jwks_uri = provider.jwks_uri
        if not (client_id and secret and token_endpoint and jwks_uri):
            raise NotFoundError("그 IdP 설정이 완전하지 않다.")
        return client_id, secret, token_endpoint, jwks_uri

    def _secret_box(self) -> SecretBox:
        return SecretBox(
            self._settings.secret_key.get_secret_value(), purpose="identity.idp.secret"
        )


@dataclass(frozen=True, slots=True)
class NewSamlProvider:
    """SAML IdP 등록 입력. SP 비밀키만 따로 받는다 — 저장 전에 봉해야 한다."""

    name: str
    entity_id: str
    sso_url: str
    certificates: tuple[str, ...]
    sp_private_key: str | None = None
    sp_certificate: str | None = None
    want_encrypted: bool = False
    allow_idp_initiated: bool = False
    email_attribute: str = "email"
    name_attribute: str = "name"
    groups_attribute: str | None = None
    jit_provisioning: bool = True
    link_verified_email: bool = True
    email_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewProvider:
    """IdP 등록 입력. 시크릿만 따로 받는다 — 저장 전에 봉해야 한다."""

    name: str
    issuer: str
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    scopes: str = "openid email profile"
    email_claim: str = "email"
    name_claim: str = "name"
    groups_claim: str | None = None
    jit_provisioning: bool = True
    link_verified_email: bool = True
    email_domains: tuple[str, ...] = ()
    trust_idp_mfa: bool = False


class SamlService:
    """SAML 2.0 SP (auth.md 4절).

    검증은 `saml` 모듈이 한다(그 안에서 python3-saml 이 한다). 여기서는 그
    앞뒤를 붙인다: 흐름을 기록해 두고, 라이브러리가 안 보는 재생을 막고,
    통과한 결과를 `SsoService` 에 넘긴다 — OIDC 와 같은 자리로 들어간다.

    **ACS 는 화면이 부르는 자리가 아니다.** IdP 가 브라우저를 통해 POST 하므로
    응답 본문을 화면이 읽을 수 없다. 그래서 검증을 통과하면 1회용 코드를
    만들어 주소로 넘기고, 화면이 그것을 토큰으로 바꾼다. 토큰을 주소에 실으면
    브라우저 기록과 리퍼러에 남는다.
    """

    #: 사람이 IdP 화면에 머무는 시간. 넉넉하되 무한하지 않게.
    FLOW_TTL_SECONDS = 15 * 60
    #: 핸드오프는 리다이렉트 한 번이다. 짧게 둔다.
    HANDOFF_TTL_SECONDS = 120

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._s = session
        self._settings = settings
        self._providers = IdentityProviderRepository(session)
        self._flows = SamlRepository(session)
        self._sso = SsoService(session, settings)

    def sp(self) -> saml.SpEndpoints:
        return saml.sp_endpoints(self._settings.public_api_url)

    def config_for(self, provider: IdentityProvider) -> saml.IdpConfig:
        key = provider.saml_sp_key_enc
        return saml.IdpConfig(
            entity_id=provider.issuer,
            sso_url=provider.authorization_endpoint,
            certificates=tuple(str(c) for c in provider.saml_certificates),
            sp_private_key=self._secret_box().decrypt(key) if key else None,
            sp_certificate=provider.saml_sp_certificate,
            want_assertions_encrypted=provider.saml_want_encrypted,
            email_attribute=provider.email_claim,
            name_attribute=provider.name_claim,
            groups_attribute=provider.groups_claim,
        )

    async def begin(self, provider_id: UUID) -> str:
        """AuthnRequest 를 보낼 주소. 요청 ID 를 기록해 두고 돌아올 때 맞춘다."""
        provider = await self._require_provider(provider_id)
        url, request_id = saml.authn_request(self.config_for(provider), self.sp())
        self._flows.start(
            provider_id=provider.id,
            request_id=request_id,
            expires_at=in_seconds(self.FLOW_TTL_SECONDS),
        )
        return url

    async def accept_response(
        self, *, saml_response: str, relay_state: str | None, ip: str | None
    ) -> str:
        """ACS. 검증을 통과하면 1회용 핸드오프 코드를 돌려준다."""
        flow, provider, request_id = await self._flow_for(saml_response, relay_state)

        verified = saml.verify_response(
            saml_response=saml_response,
            idp=self.config_for(provider),
            sp=self.sp(),
            request_id=request_id,
        )
        # 라이브러리는 요청 하나만 본다. "전에 본 어설션" 은 우리가 막는다.
        fresh = await self._flows.remember_assertion(
            provider_id=provider.id,
            assertion_id=verified.assertion_id,
            # 어설션이 만료 시각을 안 주면 흐름 만료를 쓴다. 기록이 너무 짧게
            # 사라지면 그 사이에 재생이 통한다.
            expires_at=verified.expires_at or in_seconds(self.FLOW_TTL_SECONDS),
        )
        if not fresh:
            log.warning("auth.saml_replay_rejected", provider=str(provider.id))
            raise AuthenticationError("이미 사용된 SAML 응답이다.", code="auth.saml_replayed")

        user = await self._sso.accept(provider, verified.claims, ip=ip)
        code = secrets.token_urlsafe(32)
        flow.user_id = user.id
        flow.handoff_hash = hash_token(code)
        flow.idp_verified_mfa = verified.claims.mfa_satisfied
        flow.expires_at = in_seconds(self.HANDOFF_TTL_SECONDS)
        return code

    async def exchange(self, code: str, *, ip: str | None, user_agent: str | None) -> IssuedTokens:
        """핸드오프 코드를 세션으로. 한 번만 통한다."""
        flow = await self._flows.by_handoff(hash_token(code))
        if flow is None or flow.consumed_at is not None or flow.user_id is None:
            raise AuthenticationError(
                "로그인 결과를 확인할 수 없다.", code="auth.saml_invalid_handoff"
            )
        if flow.expires_at <= utcnow():
            raise AuthenticationError(
                "로그인 결과가 만료됐다. 다시 시도해 달라.", code="auth.saml_handoff_expired"
            )
        # 먼저 닫는다. 세션을 열다 실패해도 코드는 소진돼야 한다.
        flow.consumed_at = utcnow()

        user = await UserRepository(self._s).get(flow.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("계정을 사용할 수 없다.")
        return await self._sso.open_session(
            user, ip=ip, user_agent=user_agent, idp_verified_mfa=flow.idp_verified_mfa
        )

    async def metadata(self, provider_id: UUID) -> str:
        """IdP 에 등록할 SP 메타데이터. 값을 손으로 옮겨 적게 하지 않는다."""
        provider = await self._require_provider(provider_id)
        return saml.metadata_xml(self.config_for(provider), self.sp())

    async def _flow_for(
        self, saml_response: str, relay_state: str | None
    ) -> tuple[SamlFlow, IdentityProvider, str | None]:
        """어느 흐름의 응답인가.

        RelayState 가 있으면 우리가 시작한 흐름이다. 없으면 IdP 화면에서
        시작한 것이고, 그건 **허용한 IdP 에서만** 받는다 — `InResponseTo` 가
        없으면 우리가 시작한 흐름과 묶을 수 없어, 남이 시킨 로그인을 그대로
        태우게 된다(로그인 CSRF).
        """
        if relay_state:
            flow = await self._flows.by_request_id(relay_state)
            if flow is None or flow.expires_at <= utcnow():
                raise AuthenticationError(
                    "로그인 요청을 확인할 수 없다.", code="auth.saml_unknown_request"
                )
            if flow.handoff_hash is not None:
                # 이 흐름은 이미 응답을 받았다. 두 번째는 재생이다.
                raise AuthenticationError("이미 사용된 로그인 요청이다.", code="auth.saml_replayed")
            provider = await self._require_provider(flow.provider_id)
            return flow, provider, flow.request_id

        issuer = saml.peek_issuer(saml_response)
        provider = await self._provider_by_issuer(issuer)
        if not provider.saml_allow_idp_initiated:
            raise AuthenticationError(
                "이 IdP 는 IdP 에서 시작하는 로그인을 허용하지 않는다.",
                code="auth.saml_idp_initiated_not_allowed",
            )
        flow = self._flows.start(
            provider_id=provider.id,
            request_id=None,
            expires_at=in_seconds(self.HANDOFF_TTL_SECONDS),
        )
        await self._s.flush()
        return flow, provider, None

    async def _provider_by_issuer(self, issuer: str | None) -> IdentityProvider:
        if issuer:
            for provider in await self._providers.enabled():
                if provider.kind == "saml" and provider.issuer == issuer:
                    return provider
        raise AuthenticationError(
            "그 발급자의 IdP 가 등록돼 있지 않다.", code="auth.saml_unknown_issuer"
        )

    async def _require_provider(self, provider_id: UUID) -> IdentityProvider:
        provider = await self._providers.get(provider_id)
        if provider is None or not provider.is_enabled:
            raise NotFoundError("그 IdP 를 찾을 수 없다.")
        if provider.kind != "saml":
            raise NotFoundError("그 IdP 는 SAML 이 아니다.")
        if not provider.saml_certificates:
            # 인증서가 없으면 **무엇도 검증할 수 없다.** DB 의 CHECK 가 막지만
            # 여기서도 본다 — 검증 없이 지나가는 길을 만들지 않는다.
            raise NotFoundError("그 IdP 에 서명 인증서가 없다.")
        return provider

    def _secret_box(self) -> SecretBox:
        return SecretBox(
            self._settings.secret_key.get_secret_value(), purpose="identity.idp.secret"
        )


class IdentityProviderService:
    """IdP 등록·변경 (auth.md 4절).

    IdP 설정을 쥐면 **누구로든 로그인할 수 있다.** 발급자와 JWKS 를 바꾸면
    자기 키로 서명한 토큰이 통과한다. step-up 이 붙어 있는 이유다.
    """

    def __init__(
        self, session: AsyncSession, settings: Settings, permissions: PermissionService
    ) -> None:
        self._s = session
        self._settings = settings
        self._perms = permissions
        self._providers = IdentityProviderRepository(session)

    async def list_all(self, actor: Actor) -> list[IdentityProvider]:
        """**꺼진 것도 준다.** 관리 화면에서 사라지면 다시 켤 방법이 없고,
        같은 발급자로 새로 등록하는 길도 유일 제약이 막는다 — 끄는 순간
        그 IdP 가 영구히 손에서 벗어난다."""
        await self._require(actor)
        return await self._providers.all()

    async def create(self, actor: Actor, new: NewProvider) -> IdentityProvider:
        await self._require(actor)
        # 같은 (발급자, 클라이언트) 는 하나뿐이다. 먼저 물어보지 않으면 두 번째
        # 등록이 유일 제약에 걸려 500 으로 떨어진다 — 화면에는 "내부 오류" 만
        # 뜨고, 관리자는 무엇이 겹쳤는지 알 수 없다.
        duplicate = await self._providers.find(new.issuer, new.client_id)
        if duplicate is not None:
            raise ConflictError(
                "이 발급자와 클라이언트로 이미 등록돼 있다.",
                code="identity.idp_already_registered",
                details={"provider_id": str(duplicate.id), "is_enabled": duplicate.is_enabled},
            )
        box = SecretBox(self._settings.secret_key.get_secret_value(), purpose="identity.idp.secret")
        provider = self._providers.add(
            IdentityProvider(
                name=new.name,
                kind="oidc",
                is_enabled=True,
                issuer=new.issuer,
                client_id=new.client_id,
                # 평문은 여기서 끝난다. 응답에도 로그에도 다시 나오지 않는다.
                client_secret_enc=box.encrypt(new.client_secret),
                authorization_endpoint=new.authorization_endpoint,
                token_endpoint=new.token_endpoint,
                jwks_uri=new.jwks_uri,
                scopes=new.scopes,
                email_claim=new.email_claim,
                name_claim=new.name_claim,
                groups_claim=new.groups_claim,
                jit_provisioning=new.jit_provisioning,
                link_verified_email=new.link_verified_email,
                email_domains=list(new.email_domains),
                trust_idp_mfa=new.trust_idp_mfa,
            )
        )
        await self._s.flush()
        AuditRepository(self._s).record(
            action=audit.IDP_CREATED,
            actor_id=actor.user_id,
            target_type="identity_provider",
            target_id=provider.id,
            metadata={"name": provider.name, "issuer": provider.issuer},
        )
        return provider

    async def create_saml(self, actor: Actor, new: NewSamlProvider) -> IdentityProvider:
        """SAML IdP 를 등록한다. OIDC 와 같은 step-up 을 요구한다.

        인증서가 없으면 **무엇도 검증할 수 없다.** 서비스에서 먼저 막는다 —
        DB 의 CHECK 가 최종 방어선이지만, 거기서 걸리면 사용자에게는 "내부
        오류" 로만 보인다.
        """
        await self._require(actor)
        if not new.certificates:
            raise ValidationError(
                "서명 인증서가 필요하다.", code="identity.saml_certificate_required"
            )
        if new.want_encrypted and not new.sp_private_key:
            # 암호화를 요구하면서 열 키가 없으면 모든 로그인이 실패한다.
            raise ValidationError(
                "어설션 암호화를 요구하려면 SP 비밀키가 필요하다.",
                code="identity.saml_sp_key_required",
            )

        duplicate = await self._providers.find_saml(new.entity_id)
        if duplicate is not None:
            raise ConflictError(
                "이 발급자로 이미 등록돼 있다.",
                code="identity.idp_already_registered",
                details={"provider_id": str(duplicate.id), "is_enabled": duplicate.is_enabled},
            )

        box = SecretBox(self._settings.secret_key.get_secret_value(), purpose="identity.idp.secret")
        provider = self._providers.add(
            IdentityProvider(
                name=new.name,
                kind="saml",
                is_enabled=True,
                issuer=new.entity_id,
                authorization_endpoint=new.sso_url,
                saml_certificates=list(new.certificates),
                # 평문 비밀키는 여기서 끝난다.
                saml_sp_key_enc=box.encrypt(new.sp_private_key) if new.sp_private_key else None,
                saml_sp_certificate=new.sp_certificate,
                saml_want_encrypted=new.want_encrypted,
                saml_allow_idp_initiated=new.allow_idp_initiated,
                email_claim=new.email_attribute,
                name_claim=new.name_attribute,
                groups_claim=new.groups_attribute,
                jit_provisioning=new.jit_provisioning,
                link_verified_email=new.link_verified_email,
                email_domains=list(new.email_domains),
            )
        )
        await self._s.flush()
        AuditRepository(self._s).record(
            action=audit.IDP_CREATED,
            actor_id=actor.user_id,
            target_type="identity_provider",
            target_id=provider.id,
            metadata={"name": provider.name, "issuer": provider.issuer, "kind": "saml"},
        )
        return provider

    async def update_saml(
        self,
        actor: Actor,
        provider_id: UUID,
        *,
        entity_id: str | None,
        sso_url: str | None,
        certificates: tuple[str, ...],
    ) -> IdentityProvider:
        """서명 인증서·SSO 주소를 갈아 끼운다.

        **발급자는 바꾸지 않는다.** 발급자가 다르면 다른 IdP 이고, 그 자리에
        새 신뢰 기준을 밀어 넣으면 기존 `user_identity` 가 엉뚱한 IdP 에
        묶인다 — 남의 계정으로 들어가는 길이 된다.
        """
        await self._require(actor)
        provider = await self._providers.get(provider_id)
        if provider is None or provider.kind != "saml":
            raise NotFoundError("그 SAML IdP 를 찾을 수 없다.")
        if entity_id and entity_id != provider.issuer:
            raise ConflictError(
                "발급자가 다르다. 다른 IdP 로 등록해야 한다.",
                code="identity.saml_issuer_mismatch",
                details={"registered": provider.issuer, "given": entity_id},
            )
        if not certificates and not sso_url:
            raise ValidationError("바꿀 것이 없다.", code="identity.saml_nothing_to_update")

        if certificates:
            provider.saml_certificates = list(certificates)
        if sso_url:
            provider.authorization_endpoint = sso_url
        AuditRepository(self._s).record(
            action=audit.IDP_UPDATED,
            actor_id=actor.user_id,
            target_type="identity_provider",
            target_id=provider.id,
            metadata={"certificates": len(provider.saml_certificates), "sso_url": bool(sso_url)},
        )
        return provider

    async def set_enabled(self, actor: Actor, provider_id: UUID, *, enabled: bool) -> None:
        await self._require(actor)
        provider = await self._providers.get(provider_id)
        if provider is None:
            raise NotFoundError("그 IdP 를 찾을 수 없다.")
        provider.is_enabled = enabled
        AuditRepository(self._s).record(
            action=audit.IDP_UPDATED,
            actor_id=actor.user_id,
            target_type="identity_provider",
            target_id=provider.id,
            metadata={"is_enabled": enabled},
        )

    async def _require(self, actor: Actor) -> None:
        await self._perms.require(self._s, actor, perms.IDP_MANAGE, scope=Scope.global_())
