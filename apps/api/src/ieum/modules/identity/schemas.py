"""identity 요청·응답 스키마. 라우터에서만 쓴다."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
