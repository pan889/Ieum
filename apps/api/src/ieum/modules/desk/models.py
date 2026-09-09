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

#: 승인에 필요한 동의 수 (C12).
#:
#: `one` — 한 사람이면 된다(대표 승인).
#: `all` — 명단 전원. **거절은 한 사람으로 끝난다** — 나머지에게 물어봐야
#:         답이 달라지지 않고, 물어보는 동안 요청이 멈춰 있다.
APPROVAL_MODES = ("one", "all")

#: 승인의 상태.
#:
#: `cancelled` 가 필요한 이유: 명단이 비거나(그룹이 비었다) 요청자가 물러선
#: 경우에 상담원이 문을 열 길이 있어야 한다. 없으면 그 티켓은 영원히 멈춘다.
APPROVAL_STATUSES = ("pending", "approved", "declined", "cancelled")

#: 승인자가 낼 수 있는 결정.
APPROVAL_DECISIONS = ("approve", "decline")

#: 자산이 지금 어떤 처지인가 (C15). **고정 어휘다.**
#:
#: 데이터로 두지 않는 이유: 이 값은 아무것도 굴리지 않는다(워크플로우가 아니다).
#: 설치마다 어휘가 다르면 "수리 중인 장비 몇 대" 를 설치 사이에서 비교할 수
#: 없고, 그러면 이 열은 자유 메모와 같아진다.
#:
#: `retired` 가 삭제를 대신한다. 자산은 지워지지 않는다 — 지난 티켓이
#: 그 자산을 가리키고 있고, 그 이력이 이 기능의 값이다.
ASSET_STATUSES = ("in_use", "spare", "repair", "retired")

#: 메일이 오간 방향. 받은 것과 보낸 것을 한 테이블에 두는 이유는 스레드를
#: 잇는 근거(`message_id`)가 양쪽에 다 필요하기 때문이다 — 우리가 보낸 메일의
#: id 를 모르면 고객의 회신이 어디에 붙는지 알 수 없다.
EMAIL_DIRECTIONS = ("inbound", "outbound")


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
    #: 고객이 제목을 적는 동안 문서를 추천할 지식베이스 스페이스 (C8).
    #:
    #: **`kind = "kb"` 인 스페이스만 걸 수 있다.** 팀 스페이스를 걸면 내부
    #: 문서가 고객 화면에 뜬다 — 스페이스의 종류가 "이건 고객에게 보여도
    #: 된다" 는 관리자의 선언이고, 그 선언 없이 노출하지 않는다.
    #:
    #: SET NULL 이다: 스페이스를 지우는 것이 폼을 못 쓰게 만들면 안 된다.
    #: 추천이 사라질 뿐이고, 요청은 그대로 들어온다.
    kb_space_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("space.id", ondelete="SET NULL"), nullable=True
    )
    #: 이 유형의 요청은 **승인을 받아야 처리된다** (C12).
    #:
    #: `{"mode": "one"|"all", "user_ids": [...], "group_ids": [...]}`. 비어
    #: 있으면(`NULL`) 승인이 없는 유형이다 — 대부분이 그렇다.
    #:
    #: 규칙을 여기 두는 이유: 워크플로우 상태에 두는 길도 있었지만 워크플로우는
    #: 이 제품에서 아직 **읽기 전용**이다(`workflows_router` 에 GET 만 있다).
    #: 켤 수 있는 화면이 없는 설정은 없는 설정과 같다.
    approval: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: 끄면 폼 목록에서 사라진다. 지우는 것과 다르다 — 이미 만들어진 티켓의
    #: `ticket_ext.request_type_id` 가 살아 있어야 "어떤 폼으로 들어왔나" 를
    #: 나중에도 말할 수 있다.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("portal_id", "name", name="uq_request_type_portal_id_name"),
        Index("ix_request_type_portal_id", "portal_id"),
        Index("ix_request_type_issue_type_id", "issue_type_id"),
        Index("ix_request_type_kb_space_id", "kb_space_id"),
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
    #: 이 주소로 보낸 메일이 반송된 시각 (C6).
    #:
    #: **더 보내지 않는다.** 게스트 주소는 검증되지 않은 값이고, 반송은 그
    #: 주소가 틀렸다는 유일한 신호다 — 계속 보내면 남의 주소를 적어 넣은
    #: 경우 그 사람에게 계속 배달을 시도하게 된다. 상담원 화면에도 보여
    #: 준다: 답을 썼는데 아무 것도 안 나가는 것을 모르면 안 된다.
    email_bounced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 1~5. 아직 응답이 없으면 NULL 이다 (C11).
    csat_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    csat_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 만족도 조사를 보낸 시각 (C11).
    #:
    #: **한 번만 보내는 근거다.** "아직 점수가 없음" 을 근거로 삼으면, 답하지
    #: 않은 고객은 티켓이 다시 열렸다 닫힐 때마다 또 받는다 — 안 그래도 안
    #: 답한 사람에게 조르는 꼴이다.
    csat_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    #: 목표를 얼마나 썼을 때 무엇을 하는가 (C5):
    #: `[{"at_percent": 75, "action": "notify", "user_id": "..."}]`.
    #:
    #: **초가 아니라 %로 적는다.** 목표 시간은 우선순위·요청 유형마다 다르다 —
    #: "3시간 남았을 때" 는 4시간 목표에서는 45분 만에, 3일 목표에서는 거의
    #: 끝에 걸린다. 관리자가 뜻한 것은 둘 중 하나뿐이다.
    #:
    #: 조치는 **등록된 이름 + 파라미터**로만 저장한다. 워크플로우 엔진과 같은
    #: 규약이다 — 임의 코드를 저장하고 실행하는 길을 만들지 않는다.
    escalations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
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

    **`target_at` 을 저장한다.** 매번 계산하면 관리자가 달력이나 목표 시간을
    고치는 순간 지난 티켓의 위반 여부가 조용히 바뀐다 — 어제 지킨 약속이
    오늘 깨진 것이 된다. **설정 변경은 이 값을 움직이지 않는다.**

    그것과 구별해야 하는 것이 하나 있다: **이 티켓에서 실제로 일어난 일은
    목표를 움직인다.** 고객 답변을 기다려 시계가 멈췄다가 다시 돌면, 기다린
    업무 시간만큼 목표를 미룬다(`clock.py`). 안 미루면 고객이 사흘 뒤에 답한
    티켓은 답하자마자 위반이다.

    `paused_seconds` 는 **기록**이다 — 계산에 쓰지 않는다. 잔여 시간은
    `target_at` 하나로 나오고(`sla.remaining`), 이 값은 "닷새 중 사흘은 고객을
    기다렸다" 를 말하기 위해 남긴다. 멈춘 구간을 행으로 쌓지 않는 이유는 큐
    목록의 행마다 그 행들을 더하면 조회가 폭발하기 때문이다.
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
    #: 이 티켓에 한 약속의 크기(업무 초). **클럭을 걸 때 정하고 그대로 둔다.**
    #:
    #: `target_at` 에서 거꾸로 계산할 수도 있지만 그러려면 달력이 필요하고,
    #: 멈춤으로 목표가 밀린 뒤에는 원래 약속이 얼마였는지 알 수 없게 된다.
    #: 에스컬레이션이 "목표의 몇 %" 를 재려면 이 값이 있어야 한다.
    goal_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: 이미 실행한 에스컬레이션 규칙의 이름(`"75:notify"`).
    #:
    #: **스윕은 15초마다 돈다.** 표시를 안 남기면 담당자는 15초마다 호출된다.
    #: 이름을 번호가 아니라 내용으로 만드는 이유는 `sla.EscalationRule.key`
    #: 에 적었다 — 순서를 바꾸면 이미 한 일이 안 한 것으로 보인다.
    escalated: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list, server_default="{}"
    )

    __table_args__ = (
        # 스윕이 "안 끝났고 안 알린" 것만 훑는다. 티켓이 쌓이면 이 인덱스가
        # 없는 스윕은 테이블 전체를 읽는다.
        Index(
            "ix_sla_clock_pending",
            "target_at",
            postgresql_where=text("completed_at IS NULL AND breached_at IS NULL"),
        ),
        # 에스컬레이션 스윕은 "안 끝났고 안 멈춘" 것을 훑는다. 위반 스윕과
        # 조건이 다르다(`breached_at` 을 안 본다 — 위반 뒤의 조치도 있다).
        Index(
            "ix_sla_clock_running",
            "target_at",
            postgresql_where=text("completed_at IS NULL AND paused_at IS NULL"),
        ),
        Index("ix_sla_clock_policy_id", "policy_id"),
    )


class EmailChannel(Entity, Archivable):
    """메일로 들어오는 창구 하나 (feature-map C6).

    포털과 나란한 개념이다: 프로젝트 하나에 붙고, 들어온 것을 어떤 요청
    유형으로 만들지 정한다.

    **비밀은 JSONB 에 넣지 않는다.** data-model.md 는 `inbound` 한 칸에
    "IMAP/SES 설정" 을 적어 두었지만, 그 안에 비밀번호가 들어가면 설정을
    되돌려주는 API·로그·오류 보고에 그대로 실린다. 비밀번호만 따로 뽑아
    `SecretBox` 로 암호화하고, JSONB 에는 호스트·포트·폴더처럼 보여도 되는
    것만 남긴다 (conventions.md 보안 규칙: 비밀은 로그에 남기지 않는다).
    """

    __tablename__ = "email_channel"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    #: 고객이 메일을 보내는 주소. 이 주소로 받은 것이 이 채널의 티켓이 된다.
    address: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    #: 우리가 보낼 때 쓰는 From. 받는 주소와 다를 수 있다(별칭·릴레이).
    outbound_from: Mapped[str] = mapped_column(String(320), nullable=False)
    #: 호스트·포트·사용자·폴더·TLS. **비밀번호는 여기 없다.**
    inbound: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: IMAP 비밀번호. `SecretBox(purpose="desk.email")` 로 암호화해 둔다.
    inbound_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 메일로 온 요청이 어떤 폼의 티켓이 되는가. RESTRICT 다 — 쓰이는 중인
    #: 요청 유형을 지우면 들어온 메일이 만들 수 없는 티켓을 약속하게 된다.
    default_request_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("request_type.id", ondelete="RESTRICT"), nullable=False
    )
    #: 끄면 폴링하지 않는다. 지우는 것과 다르다 — 이미 만들어진 티켓의
    #: `email_message` 행이 살아 있어야 스레드를 이을 수 있다.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 마지막 폴링에서 무엇이 잘못됐는가. 비어 있으면 정상이다.
    #:
    #: **화면에 보여 준다.** 비밀번호가 틀렸거나 서버가 막혔을 때 조용히
    #: 아무 메일도 안 들어오면, 관리자는 "고객이 안 보냈나" 로 읽는다.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_email_channel_project_id", "project_id"),
        Index("ix_email_channel_request_type_id", "default_request_type_id"),
    )


class EmailMessage(Entity):
    """오간 메일 한 통의 기록.

    **본문을 담지 않는다.** 표시용 본문은 이슈의 코멘트가 되고, 원문은
    오브젝트 스토리지에 그대로 들어간다(`raw_key`) — 인용을 지운 것이 실은
    필요했던 경우가 반드시 생기고, 그때 돌아갈 곳이 있어야 한다. 그리고 메일
    원문은 첨부까지 담은 수 MB 짜리라 DB 에 둘 것이 아니다.

    `message_id` 가 **유일하다.** IMAP 은 같은 메일을 두 번 줄 수 있고(폴링
    중 연결이 끊기면), 그때 티켓에 같은 코멘트가 둘 생긴다.
    """

    __tablename__ = "email_message"

    #: 어느 티켓의 대화인가. 채널을 못 찾아 티켓을 못 만든 메일도 기록하므로
    #: 비어 있을 수 있다 — 그 경우가 "왜 이 메일이 안 들어왔나" 의 답이다.
    issue_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=True
    )
    channel_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("email_channel.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[str] = mapped_column(String(998), nullable=False, unique=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    #: 오브젝트 스토리지의 키. 원문 그대로.
    raw_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: 티켓에 반영을 끝낸 시각. 비어 있으면 기록만 하고 넘긴 것이다.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 반영하지 않은 이유(반송·자동 응답·주소 불일치). 비어 있으면 반영했다.
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(direction.in_(EMAIL_DIRECTIONS), name="email_message_direction"),
        Index("ix_email_message_issue_id", "issue_id"),
        # 스레드 매칭이 이 컬럼으로 찾는다. 없으면 회신마다 테이블 전체를 읽는다.
        Index("ix_email_message_in_reply_to", "in_reply_to"),
    )


class AutomationRule(Entity, Archivable):
    """조건-조치 규칙 하나 (feature-map C9).

    **트리거는 이벤트다.** SLA 에스컬레이션과 다른 점이 그것이다 — 거기서는
    "아무 일도 안 일어나서" 조건이 성립하므로 스윕이 필요했고, 여기서는
    무언가 일어난 것이 규칙을 깨운다.

    `position` 이 실행 순서다. 순서가 보이는 것이 중요하다: 우선순위를 올리는
    규칙과 담당자를 정하는 규칙이 둘 다 걸릴 때, 관리자는 자기가 적은 순서대로
    돌기를 기대한다.
    """

    __tablename__ = "automation_rule"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: `{"event": "desk.ticket.submitted"}`.
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: `[{"field": "priority", "op": "gte", "value": 4}]`. **전부 맞아야 한다.**
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: `[{"kind": "assign", "user_id": "..."}]`. 등록된 이름 + 파라미터만.
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 끄면 안 돈다. 지우는 것과 다르다 — 잠시 멈추고 싶은 것이 대부분이고,
    #: 지우면 조건과 조치를 다시 적어야 한다.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_automation_rule_project_id_name"),
        Index("ix_automation_rule_project_id", "project_id"),
    )


class Approval(Entity):
    """티켓 하나에 대한 승인 요청 (C12).

    **승인자를 찍어 둔다** (`approver_ids`). 그룹을 그때그때 펼치지 않는
    이유는 `approvals.py` 머리에 적어 뒀다: 그룹은 바뀌고, 바뀌면 `all` 모드의
    셈과 "누구를 기다렸나" 가 함께 사라진다.

    한 티켓에 **기다리는 승인은 하나뿐**이다. 부분 유니크 색인으로 DB 가
    막는다 — 애플리케이션에서만 막으면 요청이 두 번 들어올 때 둘 다 통과하고,
    그러면 한쪽만 승인된 채로 문이 열린다.
    """

    __tablename__ = "approval"

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), nullable=False
    )
    #: 어느 폼이 이 승인을 요구했나. SET NULL 이다 — 유형을 지운다고 이미
    #: 받은 승인 기록이 사라지면 안 된다.
    request_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("request_type.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    #: 찍어 둔 승인자 명단. 비어 있을 수 있다(그룹이 비었거나 요청자뿐이었다) —
    #: 그때 화면은 "승인자가 없다" 를 보여 주고 상담원이 취소한다.
    #:
    #: "내가 승인할 것" 은 이 열을 `@>` 로 훑는다. **GIN 색인을 아직 두지
    #: 않는다** — 기다리는 승인은 설치 전체에서 열려 있는 요청 수만큼이고
    #: (수십 개 규모), 그 부분집합만 훑는다. 그 수가 수천이 되는 날 둔다.
    approver_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 취소한 사람. 취소는 사람이 문을 여는 일이라 누가 했는지 남는다.
    cancelled_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(mode.in_(APPROVAL_MODES), name="approval_mode"),
        CheckConstraint(status.in_(APPROVAL_STATUSES), name="approval_status"),
        # 기다리는 승인은 티켓당 하나. **부분 유니크다** — 끝난 승인은 여러
        # 개 쌓인다(거절 후 다시 요청하는 흐름이 있다).
        Index(
            "uq_approval_pending_issue",
            "issue_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_approval_issue_id", "issue_id"),
    )


class ApprovalVote(Entity):
    """승인자 한 사람의 결정.

    **한 사람은 한 번만 결정한다**(유니크). 바꿀 길을 두지 않는 이유: 승인은
    기록이고, 고칠 수 있는 기록은 근거가 못 된다. 마음이 바뀌면 상담원이
    승인을 취소하고 다시 요청한다.

    결정 시각은 `created_at` 이다. 표는 만들어질 때 결정이고 그 뒤 바뀌지
    않으므로, `decided_at` 을 따로 두면 같은 값을 두 벌 들고 있게 된다.
    """

    __tablename__ = "approval_vote"

    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval.id", ondelete="CASCADE"), nullable=False
    )
    #: 사람은 지워지지 않지만(정지만 된다) FK 는 걸어 둔다. 지워진 계정의
    #: 표가 남으면 "누가 승인했나" 를 답할 수 없다.
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    #: 거절 이유. **거절에는 사실상 필요하다** — 이유 없는 거절을 받은 사람은
    #: 무엇을 고쳐 다시 낼지 모른다. 강제하지는 않는다(승인에는 필요 없다).
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(decision.in_(APPROVAL_DECISIONS), name="approval_vote_decision"),
        UniqueConstraint("approval_id", "user_id", name="uq_approval_vote_person"),
    )


class AssetType(Entity, Archivable):
    """자산의 종류 (C15). 학교면 노트북·프로젝터·교실, SaaS 면 서비스·서버.

    고정 목록으로 두지 않는 이유: 설치마다 다르다. 그리고 종류는 **관리자가
    지은 이름**이라 번역하지 않는다(i18n.md 1절) — 상태(`ASSET_STATUSES`)와
    반대인데, 그쪽은 시스템 값이다.
    """

    __tablename__ = "asset_type"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    #: 목록에 보여줄 아이콘 이름(선택). 그림 자체를 담지 않는다.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Asset(Entity):
    """자산 하나 — 장비, 서비스, 자리 (C15).

    **속성을 자유롭게 담지 않는다.** JSONB 하나를 두고 아무 키나 넣게 하면 두
    사람이 같은 것을 다르게 적고, 화면은 그것을 그릴 수 없다. 커스텀 필드
    기계를 여기까지 늘리는 것도 하지 않았다 — 그건 이슈에 붙어 있고
    (`issue_field_value`), 자산으로 넓히면 두 벌이 된다. 실제 요청이 올 때
    정의를 갖춘 채로 더한다.

    **프로젝트에 속하지 않는다.** 노트북은 조직의 것이고 프로젝트의 것이
    아니다. 그래서 권한도 전역이다(`desk.asset.view`).
    """

    __tablename__ = "asset"

    type_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_type.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 자산번호·시리얼. **있으면 유일하다.** 없는 자산이 흔하므로(교실, 서비스)
    #: NULL 을 허용하고, Postgres 의 유니크는 NULL 을 여럿 받아들인다.
    #:
    #: 저장할 때 대문자로 맞춘다. 사람이 손으로 치는 값이라 `A-1024` 와
    #: `a-1024` 가 두 자산이 되는 것을 막는다 (프로젝트 키와 같은 판단).
    tag: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_use")
    #: 지금 쓰는 사람. SET NULL 이다 — 사람이 나가도 장비는 남는다.
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: 어느 고객 조직의 것인가 (C13). "이 프로젝터는 A 학교 것" 을 적는 자리다.
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customer_organization.id", ondelete="SET NULL"), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(status.in_(ASSET_STATUSES), name="asset_status"),
        Index("ix_asset_type_id", "type_id"),
        Index("ix_asset_organization_id", "organization_id"),
        # 이름으로 찾는다. 자산은 수천 개가 될 수 있고 화면은 **검색으로만**
        # 고르게 한다 (ux-principles "잘린 선택 목록").
        Index("ix_asset_name", "name"),
    )


class AssetLink(Base):
    """이 티켓은 이 자산에 대한 것이다 (C15).

    `entity_link`(org 의 중립 링크)를 쓰지 않는다. 그쪽은 서로 모듈을 모르는
    쌍을 위한 것이고 UUID 에 타입이 없다 — 자산을 지우면 링크가 유령으로
    남는다. `desk` 는 이미 `issues.contracts` 를 부르므로 FK 를 걸 수 있다.
    """

    __tablename__ = "asset_link"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True
    )
    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("issue.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: 누가 이었나. 자산 이력을 읽을 때 "누가 이 장비를 이 건에 붙였나" 가
    #: 질문이 된다. SET NULL 이다 — 사람이 나가도 링크는 남는다.
    linked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_asset_link_issue", "issue_id"),)
