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
    #: 고객(포털 사용자)은 /api/v1/portal/* 만 접근한다 (auth.md 5절)
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: SSO 전용 계정은 비밀번호가 없다
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 조직·역할 정책과 무관하게 이 사용자에게 MFA 를 강제한다
    require_mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(status.in_(USER_STATUSES), name="user_status"),
        Index("ix_user_active", "status", postgresql_where=text("status = 'active'")),
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class UserGroup(Entity):
    __tablename__ = "user_group"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local")

    __table_args__ = (CheckConstraint(source.in_(GROUP_SOURCES), name="user_group_source"),)


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
    __tablename__ = "mfa_credential"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: TOTP 시크릿은 AES-GCM 으로, 백업 코드는 해시로 들어간다.
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: 확인 코드 입력에 성공하기 전까지 활성화하지 않는다 (auth.md 3절)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: TOTP 재사용 방지. 마지막으로 성공한 타임스텝.
    last_timestep: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 백업 코드는 1회용이다.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(kind.in_(MFA_KINDS), name="mfa_credential_kind"),
        Index("ix_mfa_credential_user_id_kind", "user_id", "kind"),
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
