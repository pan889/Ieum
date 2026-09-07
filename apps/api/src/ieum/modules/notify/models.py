"""notify ORM 모델.

알림 채널은 인앱 + 메일 + 웹훅이다 (D-23). Teams·Slack 은 웹훅으로 붙인다 —
채널마다 전용 통합을 만들면 유지보수가 곱으로 늘어난다.
"""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Entity

WATCH_TARGETS = ("issue", "project", "page", "space")
DELIVERY_STATUSES = ("pending", "delivered", "failed", "abandoned")
#: 메일을 언제 받을지. 하나짜리 스위치로 두면 "받는다/안 받는다" 밖에 못 고른다 —
#: 알림마다 한 통씩 오면 아무도 안 읽고, 결국 통째로 끈다.
EMAIL_MODES = ("instant", "daily", "off")
WEBHOOK_SCOPES = ("global", "project")


class Notification(Entity):
    """인앱 알림. 수신자 언어로 렌더된 문구를 담는다.

    렌더 시점 언어로 굳힌다. 사용자가 나중에 언어를 바꿔도 과거 알림은
    그대로 남는데, 그게 다시 렌더해서 뒤늦게 말이 바뀌는 것보다 낫다.
    """

    __tablename__ = "notification"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 클릭 시 이동할 앱 내부 경로. 절대 URL 을 저장하지 않는다(호스트가 바뀐다).
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 어떤 엔티티에 대한 알림인지. 목록에서 묶어 보여줄 때 쓴다.
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: 알림을 일으킨 사람. 자기 행동은 자기에게 알리지 않는다.
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 읽지 않은 알림 배지의 주 경로. 부분 인덱스로 작게 유지한다.
        Index(
            "ix_notification_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_notification_user_created", "user_id", "created_at"),
    )


class Watch(Entity):
    """구독. 이슈·프로젝트 단위로 알림을 받는다."""

    __tablename__ = "watch"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(target_type.in_(WATCH_TARGETS), name="watch_target_type"),
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_watch_user_target"),
        Index("ix_watch_target", "target_type", "target_id"),
    )


class NotificationPreference(Entity):
    """수신 설정. 없으면 기본값(인앱 켜짐, 메일 즉시)으로 본다."""

    __tablename__ = "notification_preference"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: `instant`(알림마다) · `daily`(하루치를 한 통으로) · `off`.
    email_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="instant", server_default=text("'instant'")
    )
    #: 마지막 다이제스트를 보낸 시각. 하루에 두 번 보내지 않기 위한 자물쇠다.
    last_digest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    #: 자기 행동에 대한 알림을 받을지. 기본은 받지 않는다.
    notify_own_actions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 끄고 싶은 이벤트 타입 목록.
    muted_events: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        CheckConstraint(email_mode.in_(EMAIL_MODES), name="notification_preference_email_mode"),
    )


class Webhook(Entity):
    """외부 전송 대상. 시크릿은 애플리케이션 레벨로 암호화한다."""

    __tablename__ = "webhook"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="global")
    #: global 이면 NULL.
    scope_id: Mapped[UUID | None] = mapped_column(nullable=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    #: HMAC-SHA256 서명 키. AES-GCM 으로 감싸 저장한다.
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    #: 구독할 이벤트 타입. 빈 목록이면 아무것도 안 보낸다(전체 구독 아님).
    events: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 연속 실패가 쌓이면 자동으로 끈다. 죽은 엔드포인트에 계속 두드리지 않는다.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        CheckConstraint(scope.in_(WEBHOOK_SCOPES), name="webhook_scope"),
        CheckConstraint("(scope = 'global') = (scope_id IS NULL)", name="webhook_scope_id_matches"),
        Index("ix_webhook_enabled", "enabled", postgresql_where=text("enabled")),
        Index("ix_webhook_scope", "scope", "scope_id"),
    )


class WebhookDelivery(Entity):
    """전송 시도 기록. UI 의 전송 로그가 이걸 읽는다."""

    __tablename__ = "webhook_delivery"

    webhook_id: Mapped[UUID] = mapped_column(
        ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False
    )
    #: 어떤 아웃박스 이벤트에서 나왔는지. 중복 전송 판별에 쓴다.
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 응답 본문 앞부분만. 전부 담으면 로그 테이블이 폭발한다.
    response_excerpt: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(status.in_(DELIVERY_STATUSES), name="webhook_delivery_status"),
        # 같은 이벤트를 같은 웹훅에 두 번 만들지 않는다.
        UniqueConstraint("webhook_id", "event_id", name="uq_webhook_delivery_event"),
        # 워커의 재시도 폴링 경로.
        Index(
            "ix_webhook_delivery_retry",
            "next_retry_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_webhook_delivery_webhook_created", "webhook_id", "created_at"),
    )
