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


class SsoProviderResponse(BaseModel):
    """로그인 화면이 버튼을 그릴 만큼만. 시크릿도 엔드포인트도 안 나간다."""

    id: UUID
    name: str


class SsoStartResponse(BaseModel):
    #: 브라우저를 여기로 보낸다.
    authorization_url: str


class SsoCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=8192)


class SsoProviderCreateRequest(BaseModel):
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
