"""desk 이벤트.

티켓 생성 자체는 `issue.created` 로 이미 나간다 — 티켓은 이슈다(ADR-0003).
여기서 그것을 한 번 더 발행하면 알림이 두 번 간다. 그래서 **데스크만 아는
사실**만 이벤트로 만든다: 어느 창구로 들어왔는지, 게스트인지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from ieum.core.events import DomainEvent, events


@events.register_event
@dataclass(frozen=True, slots=True)
class TicketSubmitted(DomainEvent):
    """포털로 요청이 들어왔다.

    `issue.created` 와 나란히 나간다. 구독자가 다르다: 이쪽은 "접수했습니다"
    메일과 SLA 클럭 시작(C4)이 듣는다.
    """

    event_type: ClassVar[str] = "desk.ticket.submitted"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    request_type_id: UUID
    #: 로그인한 고객이면 그 계정. 게스트면 None.
    reporter_customer_id: UUID | None = None
    #: 게스트가 적은 주소. **검증되지 않았다** — 이 주소로는 본문을 보내지
    #: 않는다 (service.py 의 `submit_as_guest`).
    guest_email: str | None = None
    organization_id: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class SlaBreached(DomainEvent):
    """SLA 목표를 넘겼다 (feature-map C5).

    **스윕이 낸다.** 위반은 아무 일도 일어나지 않아서 생기므로 이벤트로는 알
    수 없다 — 시간이 지났다는 사실을 누군가 주기적으로 확인해야 한다.

    알림을 여기서 직접 만들지 않고 이벤트로 내는 이유: 알림 경로가 하나여야
    한다. 두 길로 만들면 환경설정(메일 끄기·워치)이 한쪽만 적용된다.
    """

    event_type: ClassVar[str] = "desk.sla.breached"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    policy_id: UUID
    #: 담당자가 있으면 그 사람. 없으면 큐 전체가 볼 일이다.
    assignee_id: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class SlaEscalated(DomainEvent):
    """에스컬레이션 규칙이 실행됐다 (feature-map C5).

    **위반 알림과 별개다.** 위반은 "약속을 놓쳤다" 이고, 이것은 "그래서 이걸
    했다" 다. 하나로 묶으면 75% 에서 미리 부르는 규칙을 표현할 수 없다 — 그건
    아직 위반이 아니다.

    `to_user_id` 는 `notify` 규칙이 지목한 사람이다. `raise_priority` 에는
    없고, 그때 알림은 담당자에게 간다(무엇이 바뀌었는지 알아야 하는 사람이다).
    """

    event_type: ClassVar[str] = "desk.sla.escalated"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    policy_id: UUID
    #: 어느 규칙인가. `"75:notify"` 같은 이름이다.
    rule: str
    action: str
    #: 목표를 몇 % 썼을 때 실행됐는가. 규칙의 조건이 아니라 **실제 값**이다 —
    #: 워커가 밀렸으면 75% 규칙이 140% 에서 돌 수 있고, 받는 사람은 그것을
    #: 알아야 한다.
    at_percent: int
    #: `notify` 가 지목한 사람.
    to_user_id: UUID | None = None
    #: `raise_priority` 가 올린 값.
    priority: int | None = None
    assignee_id: UUID | None = None


@events.register_event
@dataclass(frozen=True)
class ApprovalRequested(DomainEvent):
    """승인을 기다린다 (feature-map C12).

    **`to_user_ids` 가 이 이벤트의 이유다.** 승인자는 워처도 담당자도 아닐 수
    있다 — 부서장이거나 고객이다. 그 사람들을 지목하지 않으면 알림은 티켓을
    보고 있던 사람들에게만 가고, 정작 결정할 사람은 아무 소식을 못 받는다.
    그러면 요청은 승인을 기다리며 영원히 멈춰 있고 아무도 그것을 모른다.

    명단이 비어 있을 수 있다(그룹이 비었거나 요청자뿐이었다). 그때도 이벤트는
    나간다 — 티켓을 보고 있는 상담원이 "승인자가 없다" 를 알아야 한다.
    """

    event_type: ClassVar[str] = "desk.approval.requested"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    #: 알림 제목이 쓴다. 키만으로는 무엇을 승인하는지 알 수 없다.
    summary: str
    approval_id: UUID
    #: 찍어 둔 승인자들. `notify` 가 이 사람들을 수신자에 더한다.
    to_user_ids: list[UUID] = field(default_factory=list)


@events.register_event
@dataclass(frozen=True)
class ApprovalDecided(DomainEvent):
    """승인이 끝났다 — 승인이든 거절이든 (feature-map C12).

    취소에는 이벤트가 없다. 취소는 상담원이 자기 화면에서 한 일이고, 그것을
    알림으로 되돌려 주면 자기가 누른 것을 자기가 통보받는다.
    """

    event_type: ClassVar[str] = "desk.approval.decided"
    aggregate_type: ClassVar[str] = "issue"

    project_id: UUID
    issue_key: str
    summary: str
    approval_id: UUID
    #: `approved` 또는 `declined`.
    status: str
    #: 마지막 표를 낸 사람. 알림에서 본인은 빠진다.
    actor_id: UUID | None = None
