"""desk ORM 모델 (docs/architecture/data-model.md desk 절).

티켓은 별도 엔티티가 아니라 **이슈다** (ADR-0003). 그래서 여기에는 티켓
테이블이 없고, 데스크 전용 정보만 `ticket_ext` 로 1:1 붙는다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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
