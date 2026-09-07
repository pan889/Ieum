"""desk 이벤트.

티켓 생성 자체는 `issue.created` 로 이미 나간다 — 티켓은 이슈다(ADR-0003).
여기서 그것을 한 번 더 발행하면 알림이 두 번 간다. 그래서 **데스크만 아는
사실**만 이벤트로 만든다: 어느 창구로 들어왔는지, 게스트인지.
"""

from __future__ import annotations

from dataclasses import dataclass
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
