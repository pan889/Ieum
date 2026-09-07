"""issues 도메인 이벤트.

페이로드에 ORM 객체를 넣지 않는다. 알림·검색 색인·웹훅이 이걸 구독한다.

**수신자 후보(assignee/reporter)를 페이로드에 싣는다.** notify 가 이슈를
되짚어 읽으면 notify → issues 방향 의존이 생기는데, 그건 의존 그래프에
없는 화살표다. 이벤트로 뒤집는 게 문서가 정한 해법이다
(overview.md 모듈 의존 그래프).
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
    reporter_id: UUID | None = None
    #: 본문에서 언급된 사용자. **이미 볼 권한을 확인한 사람만** 담는다
    #: — notify 는 이슈 ACL 을 못 보므로 여기서 걸러야 한다.
    mentioned_ids: list[UUID] = field(default_factory=list)


@events.register_event
@dataclass(frozen=True)
class IssueUpdated(DomainEvent):
    event_type: ClassVar[str] = "issue.updated"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    actor_id: UUID
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None
    #: 바뀐 필드 이름만. 값은 issue_history 에 있다.
    changed_fields: list[str] = field(default_factory=list)
    #: 본문에서 언급된 사용자. **이미 볼 권한을 확인한 사람만** 담는다
    #: — notify 는 이슈 ACL 을 못 보므로 여기서 걸러야 한다.
    mentioned_ids: list[UUID] = field(default_factory=list)


@events.register_event
@dataclass(frozen=True)
class IssueTransitioned(DomainEvent):
    """상태 전이. overview.md 의 요청 처리 흐름 예시가 이 이벤트다."""

    event_type: ClassVar[str] = "issue.transitioned"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    actor_id: UUID
    from_state: str | None
    to_state: str
    to_state_category: str
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class IssueCommented(DomainEvent):
    event_type: ClassVar[str] = "issue.commented"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    comment_id: UUID
    actor_id: UUID
    is_internal: bool = False
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None
    #: 본문에서 언급된 사용자. **이미 볼 권한을 확인한 사람만** 담는다
    #: — notify 는 이슈 ACL 을 못 보므로 여기서 걸러야 한다.
    mentioned_ids: list[UUID] = field(default_factory=list)
    #: 자동화가 만든 것인가 (desk C9).
    #:
    #: **고리를 막는 표시다.** 규칙이 코멘트를 남기면 그 코멘트가 다시
    #: `issue.commented` 를 내고, 같은 규칙이 또 걸린다. 자동화는 이 표시가
    #: 붙은 이벤트를 건너뛴다.
    automated: bool = False


@events.register_event
@dataclass(frozen=True)
class IssueArchived(DomainEvent):
    event_type: ClassVar[str] = "issue.archived"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    actor_id: UUID
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None
