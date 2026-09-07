"""identity 요청·응답 스키마. 라우터에서만 쓴다."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ieum.modules.identity.models import AuditLog


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class MFAVerifyRequest(BaseModel):
    #: TOTP 6자리 또는 백업 코드
    code: str = Field(min_length=6, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth 토큰 타입 표기
    expires_in: int
    #: True 면 MFA 를 완료하기 전까지 일반 API 가 막힌다.
    mfa_required: bool = False
    #: 강제인데 등록된 자격증명이 없다. 화면은 확인이 아니라 **등록**으로 간다 —
    #: 확인 화면을 띄우면 만들 수 없는 코드를 요구받아 계정이 잠긴다.
    mfa_enrollment_required: bool = False


class InviteRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="en", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class ProfileUpdateRequest(BaseModel):
    """본인 설정 변경. 지금은 언어만."""

    locale: str = Field(min_length=2, max_length=16)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    avatar_url: str | None = None
    locale: str
    timezone: str
    status: str
    is_customer: bool
    last_login_at: datetime | None = None


class UserPageResponse(BaseModel):
    items: list[UserResponse]
    next_cursor: str | None = None


class TOTPEnrollResponse(BaseModel):
    """등록 시작. 확인 코드를 넣기 전까지 활성화되지 않는다."""

    credential_id: UUID
    #: 수동 입력용 base32 시크릿
    secret: str
    #: 인증 앱이 읽는 otpauth:// URI
    provisioning_uri: str
    qr_svg: str


class BackupCodesResponse(BaseModel):
    """생성 직후 1회만 평문으로 보여준다. 서버는 해시만 갖는다."""

    codes: list[str]


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ip: str | None
    user_agent: str | None
    created_at: datetime
    expires_at: datetime
    mfa_satisfied_at: datetime | None
    is_current: bool = False


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    #: 권한 이름 목록. 자기가 가진 것만 담을 수 있다.
    scopes: list[str] = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiTokenIssuedResponse(BaseModel):
    """발급 응답. `token` 은 **이때만** 볼 수 있다."""

    token: ApiTokenResponse
    #: 평문. 저장하지 않으므로 다시 못 본다.
    secret: str


class AuditLogResponse(BaseModel):
    """감사 로그 한 줄.

    `model_validate` 를 쓰지 않는다. 모델의 컬럼 이름은 `metadata` 지만 파이썬
    속성은 `audit_metadata` 다 — SQLAlchemy 의 `Base.metadata` 와 부딪히기
    때문이다. `from_attributes` 로 읽으면 그 레지스트리 객체를 집어 온다.
    """

    id: UUID
    action: str
    actor_id: UUID | None
    #: 행위자의 이메일. id 만 주면 화면에서 사람을 못 알아본다.
    actor_email: str | None = None
    target_type: str | None
    target_id: UUID | None
    ip: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def of(cls, row: AuditLog, actor_email: str | None) -> AuditLogResponse:
        return cls(
            id=row.id,
            action=row.action,
            actor_id=row.actor_id,
            actor_email=actor_email,
            target_type=row.target_type,
            target_id=row.target_id,
            ip=row.ip,
            metadata=row.audit_metadata,
            created_at=row.created_at,
        )


class AuditPageResponse(BaseModel):
    items: list[AuditLogResponse]
    next_cursor: str | None = None


class WebAuthnOptionsResponse(BaseModel):
    """브라우저의 `navigator.credentials` 에 그대로 넘길 옵션.

    JSON 문자열로 낸다. 라이브러리가 만든 것을 우리가 다시 모델로 옮기면
    규격이 바뀔 때마다 두 곳을 고쳐야 하고, 한쪽만 고치면 조용히 어긋난다.
    """

    options: str


class WebAuthnResponseRequest(BaseModel):
    """인증기가 만든 응답. 브라우저가 직렬화한 것을 그대로 받는다."""

    response: str = Field(min_length=1, max_length=100_000)
    #: 사람이 기기를 알아볼 이름. 없으면 서버가 짓는다.
    label: str | None = Field(default=None, max_length=200)


class MFACredentialResponse(BaseModel):
    """등록된 2차 요소 하나. **비밀은 나가지 않는다.**"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    label: str | None
    confirmed_at: datetime | None
    last_used_at: datetime | None
    #: 다른 기기로 동기화되는가(=패스키). 잃었을 때 결과가 다르다.
    webauthn_backed_up: bool
    webauthn_transports: list[str]


class SsoProviderResponse(BaseModel):
    """로그인 화면이 버튼을 그릴 만큼만. 시크릿도 엔드포인트도 안 나간다."""

    id: UUID
    name: str
    #: 시작하는 경로가 갈린다(OIDC 는 인가 URL, SAML 은 AuthnRequest).
    #: 종류를 안 주면 화면이 둘 중 하나를 찍어야 한다.
    kind: str


class SsoStartResponse(BaseModel):
    #: 브라우저를 여기로 보낸다.
    authorization_url: str


class SsoCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=8192)


class SsoProviderCreateRequest(BaseModel):
    """OIDC IdP 등록. SAML 은 필요한 값이 달라 요청도 따로 받는다."""

    name: str = Field(min_length=1, max_length=100)
    issuer: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str = Field(min_length=1, max_length=1024)
    authorization_endpoint: str = Field(min_length=1, max_length=512)
    token_endpoint: str = Field(min_length=1, max_length=512)
    jwks_uri: str = Field(min_length=1, max_length=512)
    scopes: str = Field(default="openid email profile", max_length=512)
    email_claim: str = Field(default="email", max_length=64)
    name_claim: str = Field(default="name", max_length=64)
    groups_claim: str | None = Field(default=None, max_length=64)
    jit_provisioning: bool = True
    link_verified_email: bool = True
    email_domains: list[str] = Field(default_factory=list)
    trust_idp_mfa: bool = False


class SamlProviderCreateRequest(BaseModel):
    """SAML IdP 등록.

    `entity_id`·`sso_url`·인증서는 IdP 의 메타데이터 XML 에서 온다. 손으로
    옮겨 적게 하지 않으려면 `metadata_xml` 을 그대로 붙여도 되게 한다 —
    서버가 읽어 채운다.
    """

    name: str = Field(min_length=1, max_length=100)
    #: 붙이면 나머지를 여기서 읽는다. 아래 셋은 비워도 된다.
    metadata_xml: str | None = Field(default=None, max_length=1_000_000)
    entity_id: str | None = Field(default=None, max_length=512)
    sso_url: str | None = Field(default=None, max_length=512)
    #: 서명 인증서. **여럿 받는다** — 회전 중에는 둘이 동시에 유효하다.
    certificates: list[str] = Field(default_factory=list)

    #: 암호화된 어설션을 열 우리 키. 넣으면 암호화를 요구할 수 있다.
    sp_private_key: str | None = Field(default=None, max_length=100_000)
    sp_certificate: str | None = Field(default=None, max_length=100_000)
    want_encrypted: bool = False
    allow_idp_initiated: bool = False

    #: 속성 이름. IdP 마다 다르다.
    email_attribute: str = Field(default="email", max_length=64)
    name_attribute: str = Field(default="name", max_length=64)
    groups_attribute: str | None = Field(default=None, max_length=64)

    jit_provisioning: bool = True
    link_verified_email: bool = True
    email_domains: list[str] = Field(default_factory=list)


class SamlProviderUpdateRequest(BaseModel):
    """서명 인증서·SSO 주소 교체.

    IdP 는 키를 돌린다. 갈아 끼울 길이 없으면 교체하는 날 로그인이 끊기고,
    같은 발급자로 새로 등록하는 길은 유일 제약이 막는다 — 그 IdP 를 잃는다.
    """

    #: 붙이면 여기서 읽는다. 발급자는 바꿀 수 없다(그건 다른 IdP 다).
    metadata_xml: str | None = Field(default=None, max_length=1_000_000)
    sso_url: str | None = Field(default=None, max_length=512)
    certificates: list[str] = Field(default_factory=list)


class SamlHandoffRequest(BaseModel):
    """ACS 가 준 1회용 코드. 화면이 이걸 토큰으로 바꾼다."""

    code: str = Field(min_length=1, max_length=512)


class IdpResponse(BaseModel):
    """관리 화면용. **시크릿은 절대 나가지 않는다** — 저장도 암호문뿐이다."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: str
    is_enabled: bool
    issuer: str
    #: OIDC 만 채운다.
    client_id: str | None
    jit_provisioning: bool
    link_verified_email: bool
    email_domains: list[str]
    trust_idp_mfa: bool
    groups_claim: str | None
    #: SAML 만. 인증서 **내용은 내보내지 않는다** — 공개 값이긴 하지만 목록
    #: 화면에 필요한 것은 "몇 장 들고 있나" 뿐이다.
    saml_certificate_count: int = 0
    saml_allow_idp_initiated: bool = False
    saml_want_encrypted: bool = False

    @classmethod
    def of(cls, provider: Any) -> IdpResponse:
        """`from_attributes` 로는 안 된다 — 인증서는 개수만 내보낸다."""
        return cls(
            id=provider.id,
            name=provider.name,
            kind=provider.kind,
            is_enabled=provider.is_enabled,
            issuer=provider.issuer,
            client_id=provider.client_id,
            jit_provisioning=provider.jit_provisioning,
            link_verified_email=provider.link_verified_email,
            email_domains=list(provider.email_domains),
            trust_idp_mfa=provider.trust_idp_mfa,
            groups_claim=provider.groups_claim,
            saml_certificate_count=len(provider.saml_certificates or []),
            saml_allow_idp_initiated=provider.saml_allow_idp_initiated,
            saml_want_encrypted=provider.saml_want_encrypted,
        )
