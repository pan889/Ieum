"""desk ORM 모델 (docs/architecture/data-model.md desk 절).

티켓은 별도 엔티티가 아니라 **이슈다** (ADR-0003). 그래서 여기에는 티켓
테이블이 없고, 데스크 전용 정보만 `ticket_ext` 로 1:1 붙는다.
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
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ieum.db.base import Archivable, Base, Entity

#: 티켓이 들어온 길. 상담원이 대신 만든 것도 구별한다 — "고객이 직접 낸 것"
#: 과 "전화를 받아 상담원이 적은 것" 은 SLA·CSAT 에서 다르게 다뤄야 한다.
TICKET_CHANNELS = ("portal", "email", "agent")

#: 폼에서 이슈 자신의 컬럼으로 가는 예약 키. 매핑이 필요 없다.
RESERVED_FORM_KEYS = ("summary", "description")


class CustomerOrganization(Entity, Archivable):
    """고객 조직(학교·기관). 티켓 가시성의 단위다 (feature-map C13).

    `domains` 는 **가입 시 기본 소속을 정하는 힌트**이지 가시성의 근거가
    아니다. 근거는 `customer_membership` 행이다. 도메인으로 읽기 시점에
    소속을 계산하면, 관리자가 이 배열을 고치는 순간 "누가 어느 티켓을 보는지"
    가 조용히 바뀐다 — 텍스트 필드 하나를 편집해서 ACL 이 움직이는 것은
    사고의 모양이다.
    """

    __tablename__ = "customer_organization"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    #: 이메일 도메인 목록. 소문자로 정규화해 저장한다.
    domains: Mapped[list[str]] = mapped_column(
        ARRAY(String(253)), nullable=False, server_default="{}"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_customer_organization_domains", "domains", postgresql_using="gin"),)


class CustomerMembership(Base):
    """고객 계정 ↔ 고객 조직. **한 고객은 한 조직에만 속한다.**

    다대다로 두면 티켓을 만들 때 "이건 어느 조직 건인가" 를 폼에서 물어야
    하고, 그건 고객이 틀리는 질문이다. 두 기관을 겸하는 담당자는 계정을 둘
    쓰는 것이 정직하다 — A 학교 모자를 쓰고 있을 때 B 학교 티켓이 보이는
    것이 옳지 않기도 하다.
    """

    __tablename__ = "customer_membership"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("customer_organization.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (Index("ix_customer_membership_organization_id", "organization_id"),)


class Portal(Entity, Archivable):
    """고객이 보는 창구 하나. 프로젝트 하나에 붙는다.

    `is_public` 은 **로그인 없이 요청을 낼 수 있는가**다. 목록을 공개하는
    스위치가 아니다 — 남의 티켓은 어떤 경우에도 보이지 않는다.
    """

    __tablename__ = "portal"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: URL 에 쓰는 이름. 프로젝트 키와 달리 고객에게 읽히는 값이다.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 브랜드 색 등. 지금은 자유 JSON 이다 — 스키마를 굳히기 전에 쓰임을 본다.
    theme: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_portal_project_id", "project_id"),)


class RequestType(Entity, Archivable):
    """요청 유형. 포털의 폼 하나이고, 이슈 유형으로 내려간다 (C2).

    `form_schema` 는 **표현**만 담는다: 순서·라벨·도움말·포털에서의 필수
    여부. 값의 종류(숫자냐 선택이냐)는 담지 않는다 — 담으면 커스텀 필드
    정의와 두 벌이 되고, 어긋나는 순간 폼은 저장되는데 이슈는 그 값을 받지
    못한다. 종류는 서버가 매핑된 정의에서 읽어 폼 응답에 실어 준다.

    `field_mapping` 은 답이 **어디로 가는가**다: 폼 키 → 커스텀 필드 키.
    예약 키(`summary`/`description`)는 이슈 자신의 컬럼으로 가므로 매핑이
    없다. 그 밖의 모든 폼 필드는 매핑이 **있어야 한다** — 갈 곳 없는 답은
    버려지는 답이고, 고객은 그것을 알 수 없다.
    """

    __tablename__ = "request_type"

    portal_id: Mapped[UUID] = mapped_column(
        ForeignKey("portal.id", ondelete="CASCADE"), nullable=False
    )
    #: 이 유형으로 만든 티켓이 갖는 이슈 유형. RESTRICT 다 — 쓰이는 중인
    #: 이슈 유형을 지우면 폼이 만들 수 없는 티켓을 약속하게 된다.
    issue_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue_type.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 목록에 보여줄 아이콘 이름(선택). 그림 자체를 담지 않는다.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    form_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default='{"fields": []}'
    )
    field_mapping: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: 끄면 폼 목록에서 사라진다. 지우는 것과 다르다 — 이미 만들어진 티켓의
    #: `ticket_ext.request_type_id` 가 살아 있어야 "어떤 폼으로 들어왔나" 를
    #: 나중에도 말할 수 있다.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("portal_id", "name", name="uq_request_type_portal_id_name"),
        Index("ix_request_type_portal_id", "portal_id"),
        Index("ix_request_type_issue_type_id", "issue_type_id"),
    )


class TicketExt(Base):
    """이슈에 붙는 데스크 전용 정보. PK 가 곧 issue_id 인 1:1 확장이다.

    이슈 테이블에 컬럼을 늘리지 않는 이유는 ADR-0003 에 있다: 티켓이 아닌
    이슈가 대다수인 설치에서 열 절반이 NULL 인 넓은 테이블이 된다.
    """

    __tablename__ = "ticket_ext"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    request_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("request_type.id", ondelete="SET NULL"), nullable=True
    )
    #: 요청을 낸 고객 계정. 게스트 요청이면 NULL 이고 아래 두 컬럼이 채워진다.
    reporter_customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 게스트가 적은 주소. **검증되지 않은 값이다.** 이 주소로는 티켓의
    #: 존재와 추적 링크만 보내고 본문을 되돌려 보내지 않는다 — 남의 주소로
    #: 욕설을 제출하면 그 사람에게 욕설이 배달되기 때문이다.
    guest_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="portal")
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customer_organization.id", ondelete="SET NULL"), nullable=True
    )
    #: 1~5. 아직 응답이 없으면 NULL 이다 (C11 에서 채운다).
    csat_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    csat_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(channel.in_(TICKET_CHANNELS), name="ticket_ext_channel"),
        CheckConstraint(
            "csat_score IS NULL OR csat_score BETWEEN 1 AND 5", name="ticket_ext_csat_range"
        ),
        # 신고자와 게스트 주소가 **둘 다** 비어 있으면 누구의 요청인지 말할 수
        # 없는 티켓이 된다. 이메일 채널(C6)도 주소는 반드시 남긴다.
        CheckConstraint(
            "reporter_customer_id IS NOT NULL OR guest_email IS NOT NULL OR channel = 'agent'",
            name="ticket_ext_has_requester",
        ),
        Index("ix_ticket_ext_reporter_customer_id", "reporter_customer_id"),
        Index("ix_ticket_ext_organization_id", "organization_id"),
        Index("ix_ticket_ext_request_type_id", "request_type_id"),
        Index("ix_ticket_ext_guest_email", "guest_email"),
    )


class Queue(Entity, Archivable):
    """조건 기반 티켓 목록 (feature-map C3).

    큐는 **뷰다.** 저장하는 것은 IQL 원문 하나이고, 목록은 볼 때마다 그
    질의로 만들어진다 — 티켓을 큐에 "넣는" 행이 없다. 상태가 바뀌면 큐가
    저절로 따라오는 것이 요점이다: 사람이 티켓을 옮겨 담아야 하는 큐는
    반드시 실제 상태와 어긋난다.

    **`visible_role_ids` 를 두지 않는다** — data-model.md 는 그 컬럼을 적어
    두었지만 만들지 않았다. 이유가 둘이다.

    1. **가리는 것이 막는 것이 아니다.** 큐에서 안 보여도 그 티켓은 이슈
       목록·검색·직접 주소로 그대로 열린다. 접근 제어처럼 읽히는데 아무 것도
       막지 않는 손잡이는 없는 것보다 나쁘다 — 관리자가 그것으로 무언가를
       숨겼다고 믿게 된다.
    2. **ACL 을 배열 컬럼에 두면 텍스트 하나를 고쳐서 권한이 움직인다.**
       `customer_organization.domains` 에서 이미 같은 판단을 했다(소속은
       행이다). 권한 시스템에는 `ScopeKind.QUEUE` 가 이미 있으므로, 큐 단위
       제한이 정말 필요해지면 `Scope.queue(id)` 역할 할당이 옳은 자리다.

    지금 큐를 볼 수 있는 근거는 프로젝트의 `desk.queue.work` 다. 그리고 큐가
    내주는 티켓은 `issue.view` ACL 을 그대로 탄다 — 큐가 권한을 넓히지 않는다.
    """

    __tablename__ = "queue"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: IQL **원문**이다. 컴파일 결과를 저장하지 않는다 — 문법이 자라면 저장된
    #: 결과는 낡고, 사람이 고칠 수 있는 것은 원문뿐이다.
    iql: Mapped[str] = mapped_column(Text, nullable=False)
    #: 사이드바 순서. 작은 것이 위다.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_queue_project_id_name"),
        Index("ix_queue_project_id", "project_id"),
    )


class CannedResponse(Entity, Archivable):
    """정형 응답 (feature-map C10). 상담원이 코멘트에 끼워 넣는 조각이다.

    프로젝트 단위의 **공용** 자료다. 소유자를 두지 않는 것이 의도다: 개인
    스니펫이면 사람이 떠날 때 같이 사라지고, 그 사람이 쓰던 문구를 다음
    사람이 다시 만든다.

    본문은 마크다운이고 **치환을 하지 않는다.** `{{고객이름}}` 같은 것을
    넣기 시작하면 값이 없을 때 무엇을 내보낼지 정해야 하고, 그 답은 언제나
    "고객에게 빈칸이나 중괄호가 배달된다" 로 끝난다. 넣을 것은 상담원이
    보고 고친다 — 끼워 넣기는 **초안**이지 발송이 아니다.
    """

    __tablename__ = "canned_response"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: `/환불` 처럼 편집기에서 부르는 이름. 비워 둘 수 있다.
    shortcut: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_canned_response_project_id_name"),
        # 같은 프로젝트에 같은 단축어가 둘이면 어느 것이 나올지 사람이 알 수
        # 없다. NULL 은 여러 개 허용된다(단축어 없는 응답).
        UniqueConstraint("project_id", "shortcut", name="uq_canned_response_project_id_shortcut"),
        Index("ix_canned_response_project_id", "project_id"),
    )


#: SLA 가 재는 것. 응답까지인지 해결까지인지.
SLA_METRICS = ("first_response", "resolution")


class BusinessCalendarRow(Entity, Archivable):
    """업무 시간표 (feature-map C4). 계산은 `calendar.py` 가 한다.

    타임존을 **달력이 갖는다.** 보는 사람의 타임존으로 재면 같은 티켓의 SLA 가
    사람마다 달라진다 — SLA 는 조직이 고객에게 한 약속이고 그 시계는 하나여야
    한다.

    설치 전체에서 공유한다(프로젝트에 매달지 않는다). 업무 시간은 회사의
    성질이고, 프로젝트마다 다른 경우가 오히려 예외다.
    """

    __tablename__ = "business_calendar"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `{"0": [["09:00","12:00"],["13:00","18:00"]], ...}` — 요일(월=0) → 구간.
    #: 문자열로 담는 이유는 JSONB 에 `time` 이 없기 때문이다. 읽을 때
    #: `calendar.py` 가 파싱하고, 저장할 때 검증한다.
    working_hours: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: 통째로 쉬는 **현지 날짜**들 (`YYYY-MM-DD`). UTC 로 두면 시차만큼
    #: 어긋난 날이 쉰다.
    holidays: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), nullable=False, default=list, server_default="{}"
    )

    __table_args__ = (UniqueConstraint("name", name="uq_business_calendar_name"),)


class SlaPolicy(Entity, Archivable):
    """SLA 정책 (feature-map C4).

    `metric` 하나에 정책 하나다. "응답 4시간, 해결 3일" 은 정책 **둘**이다 —
    한 행에 둘을 담으면 클럭도 둘을 한 행에 담아야 하고, 그러면 응답이 끝나고
    해결이 남은 중간 상태를 표현할 수 없다.

    `goals` 는 조건별 목표다: `[{"priority": 5, "seconds": 3600}, ...]`.
    위에서부터 **처음 맞는 것**이 이긴다 — 순서가 규칙이므로 배열이다(사전으로
    두면 순서가 표현되지 않는다). 조건이 빈 항목이 기본값이다.
    """

    __tablename__ = "sla_policy"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    calendar_id: Mapped[UUID] = mapped_column(
        ForeignKey("business_calendar.id", ondelete="RESTRICT"), nullable=False
    )
    goals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: 이 상태에 있는 동안 시계를 멈춘다. **상태 id 다 — 이름도, category 도
    #: 아니다.**
    #:
    #: category 로 둘 수 없다: 워크플로우의 category 는 `todo`·`in_progress`·
    #: `done` 셋뿐이고 "고객 답변 대기" 가 없다. 이름으로 두면 관리자가 상태
    #: 이름을 바꾸는 순간 조용히 안 멈춘다 — 큐 조건을 `type = Request` 로
    #: 두지 않은 것과 같은 판단이다. id 는 이름을 바꿔도 그대로이고, 상태가
    #: 지워지면 그 id 가 사라져 그냥 안 멈추게 된다(틀린 답이 아니라 없는 답).
    pause_state_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, default=list, server_default="{}"
    )
    #: 켜져 있는 정책만 새 티켓에 클럭을 건다. 끄면 **이미 걸린 클럭은
    #: 그대로 둔다** — 지난 티켓의 판정이 정책을 끄는 것으로 바뀌면 안 된다.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(metric.in_(SLA_METRICS), name="sla_policy_metric"),
        UniqueConstraint("project_id", "name", name="uq_sla_policy_project_id_name"),
        Index("ix_sla_policy_project_id", "project_id"),
        Index("ix_sla_policy_calendar_id", "calendar_id"),
    )


class SlaClock(Base):
    """티켓 하나에 걸린 SLA 시계 (feature-map C4·C5).

    **`target_at` 을 저장한다.** 매번 계산하면 관리자가 달력이나 정책을 고치는
    순간 지난 티켓의 위반 여부가 조용히 바뀐다 — 어제 지킨 약속이 오늘 깨진
    것이 된다. 목표는 클럭을 걸 때 한 번 정하고 그대로 둔다.

    `paused_seconds` 는 **누적**이다. 멈춘 구간을 행으로 쌓지 않는 이유는
    잔여 시간을 보여 줄 때마다 그 행들을 다 더해야 하기 때문이다 — 큐 목록의
    행마다 그러면 조회가 폭발한다. 대신 지금 멈춰 있으면 `paused_at` 이
    차 있고, 다시 돌 때 그 차이를 누적에 더한다.
    """

    __tablename__ = "sla_clock"

    #: 이슈 + 정책이 곧 키다. 같은 정책의 시계가 한 티켓에 둘 생기면 어느
    #: 것이 진짜인지 말할 수 없다.
    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("sla_policy.id", ondelete="CASCADE"), primary_key=True
    )
    #: 티켓이 **만들어진** 시각이다. 클럭을 거는 시각이 아니다 — 아웃박스가
    #: 늦게 훑으면 그만큼 공짜 시간이 생긴다.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 지금 멈춰 있으면 그 시각. 돌고 있으면 NULL.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 약속을 지킨 시각(응답했거나 해결했다). 채워지면 시계는 끝이다.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 위반을 **알린** 시각. 알림을 두 번 보내지 않으려고 남긴다 —
    #: "위반인가" 는 `target_at` 과 지금을 비교하면 언제든 알 수 있다.
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 스윕이 "안 끝났고 안 알린" 것만 훑는다. 티켓이 쌓이면 이 인덱스가
        # 없는 스윕은 테이블 전체를 읽는다.
        Index(
            "ix_sla_clock_pending",
            "target_at",
            postgresql_where=text("completed_at IS NULL AND breached_at IS NULL"),
        ),
        Index("ix_sla_clock_policy_id", "policy_id"),
    )
