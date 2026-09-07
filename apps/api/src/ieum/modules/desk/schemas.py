"""desk 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ── 폼 정의 ─────────────────────────────────────────────────────


class FormFieldSpec(BaseModel):
    """요청 유형 폼의 필드 하나. **표현만** 담는다.

    값의 종류(`kind`)는 여기 없다. 커스텀 필드 정의가 이미 갖고 있고, 두
    벌로 두면 어긋나는 순간 폼은 저장되는데 이슈가 그 값을 못 받는다.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    #: 고객이 읽는 글자. **관리자가 입력한 데이터**이므로 번역하지 않는다
    #: (프로젝트 이름과 같다). 서버가 만드는 문구가 아니다.
    label: str = Field(min_length=1, max_length=200)
    help: str | None = Field(default=None, max_length=500)
    #: 포털에서의 필수 여부. 정의 자체가 필수면 이 값과 무관하게 필수다.
    required: bool = False


class FormSchemaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[FormFieldSpec] = Field(default_factory=list, max_length=50)


# ── 포털 (내부 관리 API) ────────────────────────────────────────


class PortalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    theme: dict[str, Any] = Field(default_factory=dict)
    is_public: bool = False


class PortalUpdateRequest(BaseModel):
    """부분 수정. `slug` 는 여기 없다 — 고객에게 배포된 URL 이다."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    theme: dict[str, Any] | None = None
    is_public: bool | None = None


class PortalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    slug: str
    description: str | None
    theme: dict[str, Any]
    is_public: bool
    is_archived: bool
    request_type_count: int


class RequestTypeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = Field(default=None, max_length=64)
    position: int = Field(default=0, ge=0, le=9999)
    form_schema: FormSchemaSpec = Field(default_factory=FormSchemaSpec)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    is_enabled: bool = True


class RequestTypeUpdateRequest(BaseModel):
    """부분 수정. `issue_type_id` 는 여기 없다.

    유형을 갈아 끼우면 그 유형에 뜨는 커스텀 필드가 달라져 폼의 매핑이 통째로
    무의미해지고, 이미 만들어진 티켓과 새 티켓이 서로 다른 워크플로우를 타게
    된다. 새로 만드는 것이 정직하다 — 커스텀 필드 정의의 `kind` 와 같은 판단.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = Field(default=None, max_length=64)
    position: int | None = Field(default=None, ge=0, le=9999)
    form_schema: FormSchemaSpec | None = None
    field_mapping: dict[str, str] | None = None
    is_enabled: bool | None = None


class RequestTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    portal_id: UUID
    issue_type_id: UUID
    issue_type_name: str
    name: str
    description: str | None
    icon: str | None
    position: int
    form_schema: dict[str, Any]
    field_mapping: dict[str, str]
    is_enabled: bool
    is_archived: bool
    ticket_count: int


# ── 고객 조직 ───────────────────────────────────────────────────


class CustomerOrgCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=2000)


class CustomerOrgUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    domains: list[str] | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)


class CustomerOrgResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    domains: list[str]
    note: str | None
    is_archived: bool
    member_count: int


class CustomerInviteRequest(BaseModel):
    """고객을 초대해 이 조직에 넣는다. `is_customer` 를 받지 않는다 — 이
    통로로 만든 계정은 언제나 고객이다."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)


class MembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID


class CustomerMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    display_name: str
    status: str


# ── 포털 (고객이 보는 API) ─────────────────────────────────────


class PortalInfoResponse(BaseModel):
    """포털의 겉모습. 로그인 없이도 볼 수 있다(`is_public` 이면)."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    theme: dict[str, Any]
    allows_guests: bool


class PortalFormFieldResponse(BaseModel):
    """폼 필드 하나. 종류·선택지는 서버가 정의에서 읽어 채운다."""

    key: str
    label: str
    help: str | None
    required: bool
    #: 커스텀 필드 종류, 또는 예약 키의 `text`/`markdown`.
    kind: str
    #: `select` 의 선택지 등. 정의의 `config` 를 그대로 준다.
    config: dict[str, Any]


class PortalRequestTypeResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    icon: str | None


class PortalFormResponse(BaseModel):
    request_type: PortalRequestTypeResponse
    fields: list[PortalFormFieldResponse]


class PortalSubmitRequest(BaseModel):
    """폼 제출. 답은 폼 키로 온다 — 커스텀 필드 키를 노출하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    request_type_id: UUID
    answers: dict[str, Any] = Field(default_factory=dict)


class GuestSubmitRequest(PortalSubmitRequest):
    """게스트 제출. 이름과 주소를 함께 받는다."""

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)


class AnswerResponse(BaseModel):
    """제출된 답 하나. **라벨이 함께 온다** — 화면이 `key` 만 받으면 고객에게
    `device` 를 보여 준다."""

    key: str
    label: str
    value: Any


class TicketResponse(BaseModel):
    """고객이 보는 티켓 한 건."""

    id: UUID
    key: str
    summary: str
    description: str | None
    state_name: str
    state_category: str
    created_at: datetime
    updated_at: datetime
    request_type_name: str | None
    #: 폼 키로 되돌린 답. 커스텀 필드 키가 아니고, 폼에 적힌 순서다.
    answers: list[AnswerResponse]


class ReplyResponse(BaseModel):
    """대화 한 줄. **`is_internal` 이 없다** — 이 표면에 오는 것은 언제나
    공개 코멘트다. 필드를 두면 언젠가 True 가 실려 나간다."""

    id: UUID
    author_id: UUID | None
    body: str
    created_at: datetime
    edited_at: datetime | None


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=100_000)


class RequesterResponse(BaseModel):
    """요청을 낸 사람. `verified=false` 면 게스트가 적어 낸 주소다 —
    검증되지 않았다는 사실을 화면이 보여 줘야 한다."""

    user_id: UUID | None
    display_name: str
    email: str
    verified: bool


class AgentTicketResponse(BaseModel):
    """상담원 화면이 쓰는 데스크 정보."""

    issue_id: UUID
    channel: str
    request_type_name: str | None
    portal_slug: str | None
    requester: RequesterResponse | None
    organization_name: str | None
    csat_score: int | None


class AgentTicketEnvelope(BaseModel):
    """`ticket` 이 `null` 이면 이 이슈는 티켓이 아니다(또는 데스크 정보를 볼
    권한이 없다). **오류가 아니라 답이다.**

    봉투로 감싸는 것은 화면이 확인을 건너뛸 수 없게 하려는 것이다. 필드가
    널 가능이므로 타입 검사가 `ticket.requester` 를 바로 읽는 코드를
    거절한다 — 404 로 답할 때 얻던 보장을 타입이 대신 산다.
    """

    ticket: AgentTicketResponse | None


class TicketSummaryResponse(BaseModel):
    id: UUID
    key: str
    summary: str
    state_name: str
    state_category: str
    created_at: datetime
    updated_at: datetime
    request_type_name: str | None
