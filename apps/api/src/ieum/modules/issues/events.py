"""issues 도메인 이벤트.

페이로드에 ORM 객체를 넣지 않는다. 알림·검색 색인·웹훅이 이걸 구독한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from ieum.core.events import DomainEvent, events


@events.register_event
@dataclass(frozen=True)
class IssueCreated(DomainEvent):
    event_type: ClassVar[str] = "issue.created"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    actor_id: UUID
    assignee_id: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class IssueUpdated(DomainEvent):
    event_type: ClassVar[str] = "issue.updated"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    actor_id: UUID
    #: 바뀐 필드 이름만. 값은 issue_history 에 있다.
    changed_fields: list[str] = field(default_factory=list)


@events.register_event
@dataclass(frozen=True)
class IssueTransitioned(DomainEvent):
    """상태 전이. overview.md 의 요청 처리 흐름 예시가 이 이벤트다."""

    event_type: ClassVar[str] = "issue.transitioned"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    actor_id: UUID
    from_state: str | None
    to_state: str
    to_state_category: str


@events.register_event
@dataclass(frozen=True)
class IssueCommented(DomainEvent):
    event_type: ClassVar[str] = "issue.commented"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    comment_id: UUID
    actor_id: UUID
    is_internal: bool = False


@events.register_event
@dataclass(frozen=True)
class IssueArchived(DomainEvent):
    event_type: ClassVar[str] = "issue.archived"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    actor_id: UUID
