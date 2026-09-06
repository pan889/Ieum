"""identity 도메인 이벤트. 페이로드에 ORM 객체를 넣지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from ieum.core.events import DomainEvent, events


@events.register_event
@dataclass(frozen=True)
class UserInvited(DomainEvent):
    event_type: ClassVar[str] = "identity.user.invited"
    aggregate_type: ClassVar[str] = "user"

    email: str
    invited_by: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class UserActivated(DomainEvent):
    event_type: ClassVar[str] = "identity.user.activated"
    aggregate_type: ClassVar[str] = "user"

    email: str


@events.register_event
@dataclass(frozen=True)
class UserLoggedIn(DomainEvent):
    event_type: ClassVar[str] = "identity.user.logged_in"
    aggregate_type: ClassVar[str] = "user"

    session_id: UUID
    ip: str | None = None


@events.register_event
@dataclass(frozen=True)
class MFAEnrolled(DomainEvent):
    event_type: ClassVar[str] = "identity.mfa.enrolled"
    aggregate_type: ClassVar[str] = "user"

    kind: str


@events.register_event
@dataclass(frozen=True)
class SessionFamilyRevoked(DomainEvent):
    """리프레시 토큰 재사용이 감지돼 세션 계열 전체를 폐기했다."""

    event_type: ClassVar[str] = "identity.session.family_revoked"
    aggregate_type: ClassVar[str] = "user"

    family_id: UUID
    reason: str
