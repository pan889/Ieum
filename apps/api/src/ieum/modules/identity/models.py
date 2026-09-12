"""identity ORM 모델 (docs/architecture/data-model.md identity 절)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Entity

USER_STATUSES = ("invited", "active", "suspended")
MFA_KINDS = ("totp", "webauthn", "backup_code")
GROUP_SOURCES = ("local", "scim", "idp")


class User(Entity):
    __tablename__ = "user"

    # citext 확장으로 대소문자 무시 unique 를 건다. 확장이 없는 환경(테스트)에서도
    # 돌아가야 하므로 애플리케이션에서도 소문자로 정규화해 저장한다.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 언어 결정 순서: 사용자 설정 → 조직 기본값 → Accept-Language → en
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="invited")
    #: 정지 직전 상태. 되살릴 때 여기로 돌아간다. 정지 중이 아니면 `NULL`.
    #:
    #: **비밀번호 유무로는 갈릴 수 없어서 기억해 둔다.** `password_hash` 가
    #: 없는 계정은 두 가지다 — 초대만 받고 수락하지 않은 사람과, IdP 로
    #: 들어오는 SSO·SCIM 계정. 앞쪽은 되살릴 때 `invited` 여야 하고 뒤쪽은
    #: `active` 여야 하는데, 열 하나로는 구분이 안 된다. 잘못 갈라서 SSO
    #: 계정이 `invited` 가 되면 그 사람은 영영 못 들어온다.
    pre_suspend_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 고객(포털 사용자)은 /api/v1/portal/* 만 접근한다 (auth.md 5절)
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: SSO 전용 계정은 비밀번호가 없다
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 조직·역할 정책과 무관하게 이 사용자에게 MFA 를 강제한다
    require_mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: 이 계정을 밀어 넣은 IdP. `NULL` 이면 SCIM 이 만든 계정이 아니다.
    #:
    #: **누가 만들었는지를 적어 두는 이유는 지우는 쪽 때문이다.** IdP 가
    #: 둘이면(예: 협력사) 한쪽의 토큰으로 다른 쪽 계정을 비활성화할 수 있으면
    #: 안 된다 — 프로비저닝은 조용히 돌고, 그런 사고는 며칠 뒤에 발견된다.
    scim_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="SET NULL"), nullable=True
    )
    #: 그 IdP 가 이 사람을 부르는 id (`externalId`). 우리 id 와 다르다.
    scim_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(status.in_(USER_STATUSES), name="user_status"),
        Index("ix_user_active", "status", postgresql_where=text("status = 'active'")),
        # 같은 IdP 가 같은 사람을 두 번 밀어 넣지 못하게 한다. 막지 않으면
        # 재시도 한 번에 계정이 둘 생기고, 둘 다 로그인할 수 있다.
        UniqueConstraint(
            "scim_provider_id", "scim_external_id", name="uq_user_scim_provider_external"
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class UserGroup(Entity):
    __tablename__ = "user_group"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    #: 이 그룹을 밀어 넣은 IdP. 사용자와 같은 이유다 — 남의 그룹을 지우지
    #: 못하게 한다.
    scim_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="SET NULL"), nullable=True
    )
    scim_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(source.in_(GROUP_SOURCES), name="user_group_source"),
        Index("ix_user_group_scim_provider_id", "scim_provider_id"),
    )


class GroupMember(Entity):
    __tablename__ = "group_member"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_group.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member_group_id_user_id"),
        Index("ix_group_member_user_id", "user_id"),
    )


class UserSession(Entity):
    """서버 상태를 가진 세션. JWT 를 쓰지 않는 이유는 즉시 무효화 때문이다."""

    __tablename__ = "session"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    #: 원문은 저장하지 않는다. 조회는 해시로 한다.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: 액세스 토큰도 서버 상태다. JWT 를 쓰면 즉시 무효화(퇴사·권한 회수)가 안 된다.
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 리프레시 로테이션 계열 식별자. 재사용이 감지되면 이 계열 전체를 폐기한다.
    family_id: Mapped[UUID] = mapped_column(nullable=False)
    #: 로테이션으로 이 세션을 대체한 세션. 폐기된 토큰의 재사용 탐지에 쓴다.
    rotated_to_id: Mapped[UUID | None] = mapped_column(nullable=True)

    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: step-up 판정 기준. 최근 MFA 확인 시각.
    mfa_satisfied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: **실제로 MFA 챌린지를 통과했는가.** `mfa_satisfied_at` 과 다르다 —
    #: 그 값은 MFA 가 필요 없는 계정에도 로그인 시점에 채워진다. step-up 은
    #: "사람이 방금 다시 증명했다" 는 뜻이라 이쪽을 봐야 한다 (auth.md 3절).
    mfa_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: WebAuthn 챌린지. **이 세션에 발급한 것만 이 세션이 쓴다.**
    #:
    #: 챌린지를 어디 두느냐가 곧 그 챌린지가 누구 것이냐를 정한다. 세션 밖에
    #: 두면(예: 사용자별) 한 창에서 받은 챌린지를 다른 창이 소진할 수 있고,
    #: 그러면 "이 브라우저가 지금 키를 만졌다" 는 보장이 사라진다.
    webauthn_challenge: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webauthn_challenge_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_session_user_id", "user_id"),
        Index("ix_session_family_id", "family_id"),
        Index(
            "ix_session_live",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class MFACredential(Entity):
    """2차 요소 하나. 종류마다 쓰는 칼럼이 갈린다 (auth.md 3절).

    TOTP·백업 코드는 **비밀**을 들고 있고(암호화·해시), WebAuthn 은 **공개키**를
    들고 있다 — 공개키는 비밀이 아니므로 `secret_enc` 에 밀어 넣지 않는다.
    이름이 거짓이 되면, 다음 사람이 그 칼럼을 비밀처럼 다루거나 그 반대가 된다.
    """

    __tablename__ = "mfa_credential"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: TOTP 시크릿은 AES-GCM 으로, 백업 코드는 해시로 들어간다. WebAuthn 은 비운다.
    secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: 확인 코드 입력에 성공하기 전까지 활성화하지 않는다 (auth.md 3절)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: TOTP 재사용 방지. 마지막으로 성공한 타임스텝.
    last_timestep: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 백업 코드는 1회용이다.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── WebAuthn 전용 ────────────────────────────────────────────
    #: 인증기가 준 자격증명 ID(base64url). 인증할 때 이것으로 찾는다.
    #: **전역 유일**이다 — 같은 자격증명이 두 계정에 붙으면, 하나로 다른
    #: 계정에 들어갈 수 있다.
    webauthn_credential_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True, unique=True
    )
    #: COSE 공개키(base64url). 서명 검증에 쓴다.
    webauthn_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 인증기의 서명 카운터. **되돌아가면 복제를 의심한다** — 0 을 계속 주는
    #: 인증기도 있어서(패스키), 0 은 검사에서 빼고 늘어난 값만 본다.
    webauthn_sign_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: 인증기가 알려 준 전송 방식(usb/nfc/ble/internal/hybrid). 화면 표시용.
    webauthn_transports: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: 다른 기기로 동기화되는 자격증명인가(=패스키). 사람에게 보여 준다 —
    #: "이 기기에만 있는 키" 와 "계정에 딸린 키" 는 잃었을 때 결과가 다르다.
    webauthn_backed_up: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(kind.in_(MFA_KINDS), name="mfa_credential_kind"),
        Index("ix_mfa_credential_user_id_kind", "user_id", "kind"),
        # 종류별로 있어야 하는 것. 반쯤 채운 행은 **누가 인증하려는 순간**
        # 드러난다 — 그때는 로그인이 안 되는 이유를 알 수 없다.
        CheckConstraint(
            "(kind = 'webauthn') OR (secret_enc IS NOT NULL)",
            name="mfa_credential_secret_required",
        ),
        CheckConstraint(
            "(kind <> 'webauthn') OR ("
            "webauthn_credential_id IS NOT NULL AND webauthn_public_key IS NOT NULL)",
            name="mfa_credential_webauthn_required",
        ),
    )


class ApiToken(Entity):
    """개인 액세스 토큰(PAT). Authorization: Bearer 로 쓴다."""

    __tablename__ = "api_token"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_api_token_user_id", "user_id"),)


class AuditLog(Entity):
    """append-only. 애플리케이션에서 UPDATE/DELETE 하지 않는다 (auth.md 6절)."""

    __tablename__ = "audit_log"

    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        Index("ix_audit_log_actor_id_created_at", "actor_id", "created_at"),
        Index("ix_audit_log_action_created_at", "action", "created_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )


class LoginAttempt(Entity):
    """로그인 실패 제한용. 계정당 + IP당 슬라이딩 윈도우 (auth.md 2절)."""

    __tablename__ = "login_attempt"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        Index("ix_login_attempt_email_created_at", "email", "created_at"),
        Index("ix_login_attempt_ip_created_at", "ip", "created_at"),
    )


#: OIDC 와 SAML 이 같은 표에 들어온다 (auth.md 4절). 쓰는 칼럼이 갈리므로
#: 종류별로 무엇이 있어야 하는지를 CHECK 로 못 박는다 — 반쯤 채운 행이
#: 들어오면 로그인하는 순간에야 드러난다.
IDP_KINDS = ("oidc", "saml")


class IdentityProvider(Entity):
    """회사 IdP 한 곳. 여러 곳을 동시에 등록할 수 있다.

    클라이언트 시크릿은 애플리케이션 레벨로 암호화해 둔다(AES-GCM). DB 를
    통째로 읽는 사고가 나도 IdP 로 위장할 수는 없어야 한다.
    """

    __tablename__ = "identity_provider"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="oidc")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: OIDC 면 ID 토큰의 `iss`, SAML 이면 IdP 의 EntityID. 양쪽 다 **정확히**
    #: 일치해야 하는 발급자 식별자라 한 칼럼에 둔다.
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    #: 브라우저가 가는 곳. OIDC 의 authorization endpoint, SAML 의 SSO URL.
    authorization_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)

    # ── OIDC 전용 ────────────────────────────────────────────────
    client_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jwks_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scopes: Mapped[str] = mapped_column(String(512), nullable=False, default="openid email profile")

    # ── SAML 전용 ────────────────────────────────────────────────
    #: IdP 의 서명 인증서. **여럿 받는다** — 회전 중에는 두 개가 동시에
    #: 유효하고, 하나만 두면 교체하는 날 로그인이 통째로 끊긴다.
    saml_certificates: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: 암호화된 어설션을 열 우리(SP) 키. 애플리케이션 레벨로 봉해 둔다.
    saml_sp_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    saml_sp_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 어설션이 암호화돼 오기를 **요구**한다. 켜면 평문 어설션을 거절한다.
    saml_want_encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: IdP 화면에서 시작하는 로그인(우리 요청 없이 오는 응답)을 받는다.
    #: 기본은 거절이다 — `InResponseTo` 가 없으면 우리가 시작한 흐름과
    #: 묶을 수 없어, 남이 시킨 로그인을 그대로 태우게 된다.
    saml_allow_idp_initiated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: 클레임·속성 이름. IdP 마다 다르다 — Entra 는 그룹을 `groups`, Okta 는 설정에 따라.
    email_claim: Mapped[str] = mapped_column(String(64), nullable=False, default="email")
    name_claim: Mapped[str] = mapped_column(String(64), nullable=False, default="name")
    groups_claim: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 최초 로그인 시 계정을 만든다. 끄면 미리 초대된 사람만 들어온다.
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 검증된 이메일이 같으면 기존 계정에 잇는다 (auth.md 4절 기본값).
    link_verified_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 이 도메인의 메일 주소를 이 IdP 로 보낸다. 비어 있으면 라우팅 안 한다.
    email_domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: IdP 가 2차 요소를 책임진다고 볼 것인가. 켜면 `amr`/`acr` 를 확인한다.
    trust_idp_mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── SCIM 프로비저닝 ─────────────────────────────────────────
    #
    # **SSO 와 같은 행에 둔다.** IdP 한 곳이 로그인과 프로비저닝을 함께
    # 담당하고(Okta·Entra 의 앱 하나가 둘 다 설정한다), 그래야 "이 IdP 를
    # 껐다" 가 로그인과 계정 밀어넣기에 같이 적용된다.
    scim_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: 프로비저닝 토큰의 SHA-256. **원문은 안 저장한다** — PAT 과 같은 규약이고,
    #: 만들 때 한 번만 보여 준다.
    scim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    scim_token_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scim_last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(kind.in_(IDP_KINDS), name="identity_provider_kind"),
        UniqueConstraint("issuer", "client_id", name="uq_identity_provider_issuer_client"),
        # 종류별로 있어야 하는 것. OIDC 는 토큰을 교환하고 JWKS 로 검증하므로
        # 그 넷이 없으면 로그인이 불가능하고, SAML 은 서명 인증서가 없으면
        # **무엇도 검증할 수 없다**. 반쯤 채운 행을 DB 가 거절한다.
        CheckConstraint(
            "(kind <> 'oidc') OR ("
            "client_id IS NOT NULL AND client_secret_enc IS NOT NULL"
            " AND token_endpoint IS NOT NULL AND jwks_uri IS NOT NULL)",
            name="identity_provider_oidc_fields",
        ),
        CheckConstraint(
            "(kind <> 'saml') OR jsonb_array_length(saml_certificates) > 0",
            name="identity_provider_saml_fields",
        ),
        # SAML 행은 `client_id` 가 비어 있어 위의 유일 제약이 걸리지 않는다
        # (Postgres 는 NULL 을 서로 다르게 본다). 발급자 하나에 하나만 둔다.
        Index(
            "uq_identity_provider_saml_issuer",
            "issuer",
            unique=True,
            postgresql_where=text("kind = 'saml'"),
        ),
    )


class UserIdentity(Entity):
    """IdP 의 한 사람 ↔ 우리 계정.

    `subject` 로 잇는다. 이메일로 이으면 IdP 에서 주소를 바꾼 사람이 남의
    계정에 들어가거나 자기 계정을 잃는다 — `sub` 는 바뀌지 않는 값이다.
    """

    __tablename__ = "user_identity"

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider_id", "subject", name="uq_user_identity_provider_subject"),
        Index("ix_user_identity_user_id", "user_id"),
    )


class SamlFlow(Entity):
    """SAML 로그인 하나. 시작부터 화면이 토큰을 받아 가기까지.

    두 가지를 한 행이 한다.

    **하나. 우리가 시작한 흐름인지 확인한다.** SP-initiated 면 AuthnRequest 의
    ID 를 여기 적어 두고, 돌아온 응답의 `InResponseTo` 와 맞춘다. 안 맞추면
    남이 받은 어설션을 우리 ACS 에 밀어 넣을 수 있다.

    **둘. ACS 와 화면 사이를 잇는다.** ACS 는 IdP 가 브라우저를 통해 POST 하는
    자리라 응답 본문을 화면이 읽지 못한다. 그래서 검증을 통과하면 1회용
    코드를 만들어 주소로 넘기고, 화면이 그것을 토큰으로 바꾼다 — OIDC 의
    인가 코드와 같은 모양이다. 토큰을 주소에 실으면 브라우저 기록에 남는다.
    """

    __tablename__ = "saml_flow"

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="CASCADE"), nullable=False
    )
    #: 우리가 보낸 AuthnRequest 의 ID. IdP 화면에서 시작한 로그인은 없다.
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    #: 검증을 통과한 뒤에 채운다. 그전에는 "누구인지 모르는 흐름" 이다.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    #: 1회용 핸드오프 코드의 해시. 원문은 저장하지 않는다.
    handoff_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    #: IdP 가 2차 요소를 책임졌는가. 세션을 여는 쪽이 이 값을 본다.
    idp_verified_mfa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 한 번 쓰면 닫는다. 두 번째 교환은 거절한다.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_saml_flow_expires_at", "expires_at"),)


class SamlSeenAssertion(Entity):
    """이미 받아들인 어설션. **재생 방지는 우리가 한다.**

    라이브러리는 요청 하나를 검증할 뿐 "이거 전에 본 것" 을 모른다. 어설션은
    유효 시간(보통 몇 분) 안에서는 서명이 계속 맞으므로, 한 번 가로챈 응답을
    그 창 안에 다시 밀어 넣으면 그대로 통과한다.

    `expires_at` 은 어설션 자신의 `NotOnOrAfter` 다. 그 시각이 지나면 서명이
    맞아도 검증에서 걸리므로 기록을 지워도 안전하다.
    """

    __tablename__ = "saml_seen_assertion"

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="CASCADE"), nullable=False
    )
    assertion_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_saml_seen_assertion_expires_at", "expires_at"),)
