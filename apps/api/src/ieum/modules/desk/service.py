"""desk 비즈니스 로직. 권한 검사와 도메인 규칙이 여기 있다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.crypto import SecretBox
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.logging import get_logger
from ieum.core.outbox import publish
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as perms
from ieum.modules.desk.calendar import CalendarError
from ieum.modules.desk.events import TicketSubmitted
from ieum.modules.desk.models import (
    RESERVED_FORM_KEYS,
    SLA_METRICS,
    BusinessCalendarRow,
    CannedResponse,
    CustomerOrganization,
    EmailChannel,
    Portal,
    Queue,
    RequestType,
    SlaClock,
    SlaPolicy,
    TicketExt,
)
from ieum.modules.desk.repository import (
    CannedResponseRepository,
    CustomerMembershipRepository,
    CustomerOrganizationRepository,
    PortalRepository,
    QueueRepository,
    RequestTypeRepository,
    TicketRepository,
    normalize_domain,
    normalize_slug,
)
from ieum.modules.desk.sla import (
    SlaError,
    parse_calendar,
    validate_escalations,
    validate_goals,
)
from ieum.modules.desk.sla import remaining as sla_remaining
from ieum.modules.desk.sla import utc as sla_utc
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import contracts as issues
from ieum.modules.org import contracts as org

log = get_logger(__name__)

#: 슬러그는 URL 조각이다. 고객이 손으로 칠 수도 있으므로 좁게 잡는다.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
#: 폼 필드 키. 커스텀 필드 키와 같은 모양이라 매핑을 눈으로 확인할 수 있다.
FORM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: 이메일은 여기서 완전히 검증하지 않는다 — 최종 검증은 메일이 도달하는지다.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

#: 예약 키가 만드는 위젯. 커스텀 필드가 아니므로 정의가 없다.
RESERVED_KINDS = {"summary": "text", "description": "markdown"}

#: 포털 폼에 올릴 수 없는 커스텀 필드 종류.
#:
#: `user` 와 `version` 은 선택지가 **내부 데이터**다 — 사람 목록과 릴리스
#: 목록. 고객에게 그것을 펼쳐 보이는 것은 내주면 안 되는 것을 내주는 일이고,
#: 애초에 고객이 담당자나 수정 버전을 고르는 모델이 아니다. 저장할 때 막지
#: 않으면 포털이 그 필드를 그릴 방법이 없어서 **화면에 없는 필수 항목**이
#: 생긴다 — 고객은 다 채웠는데 제출이 거절된다.
FORBIDDEN_FORM_KINDS = ("user", "version")


@dataclass(frozen=True, slots=True)
class PortalView:
    portal: Portal
    request_type_count: int


@dataclass(frozen=True, slots=True)
class RequestTypeView:
    request_type: RequestType
    issue_type_name: str
    ticket_count: int


@dataclass(frozen=True, slots=True)
class CustomerOrgView:
    organization: CustomerOrganization
    member_count: int


@dataclass(frozen=True, slots=True)
class FormField:
    key: str
    label: str
    help: str | None
    required: bool
    kind: str
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PortalForm:
    request_type: RequestType
    fields: list[FormField]


@dataclass(frozen=True, slots=True)
class Answer:
    """제출된 답 하나.

    `label` 이 함께 오는 이유: 화면이 `key` 만 받으면 고객에게 `device` 를
    보여 준다. 라벨은 폼 스키마에 있고, 그 스키마는 서버가 갖고 있다 —
    화면이 요청 유형을 다시 받아 짜맞추게 하면 폼이 꺼진 뒤에는 라벨을
    잃는다. 라벨이 사라진 필드는 `key` 로 대신한다: 고객이 적은 값을
    감추는 것보다 이름 없이 보여 주는 편이 낫다.
    """

    key: str
    label: str
    value: Any


@dataclass(frozen=True, slots=True)
class Requester:
    """요청을 낸 사람. 계정이 있으면 이름과 주소, 게스트면 적어 낸 값이다.

    `verified` 가 요점이다: 게스트가 적은 주소는 **검증되지 않았다.** 상담원
    화면이 그 사실을 보여 줘야 한다 — 아니면 게스트 주소를 계정 주소처럼
    믿고 그 주소로 확인 없이 무엇이든 보낸다.
    """

    user_id: UUID | None
    display_name: str
    email: str
    verified: bool


@dataclass(frozen=True, slots=True)
class SlaStanding:
    """이 티켓의 SLA 한 줄. 상담원 화면이 그린다 (C5).

    **잔여 시간을 서버가 계산해 내려 준다.** 브라우저에 목표 시각만 주고
    카운트다운하게 두면 업무 시간이 빠진다 — 금요일 저녁에 남은 4시간이
    토요일 아침에 0 이 된다.
    """

    policy_name: str
    metric: str
    target_at: datetime
    #: 남은 업무 초. 위반이면 음수다 — 화면이 "3시간 초과" 를 말해야 한다.
    remaining_seconds: int
    breached: bool
    paused: bool
    completed: bool


@dataclass(frozen=True, slots=True)
class AgentTicketView:
    """상담원이 티켓 화면에서 필요한 데스크 정보.

    이슈 응답에 이걸 섞지 않는다 — `issues` 가 `desk` 를 알게 되고, 그건
    의존 그래프에 없는 화살표다(ADR-0010). 화면이 조회를 하나 더 한다.
    """

    ticket: TicketExt
    request_type_name: str | None
    portal_slug: str | None
    requester: Requester | None
    organization_name: str | None
    #: 이 티켓에 걸린 SLA 들. 비어 있으면 정책이 없거나 아직 안 걸렸다.
    sla: list[SlaStanding] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TicketView:
    ticket: TicketExt
    issue: issues.TicketIssue
    request_type_name: str | None
    #: 커스텀 필드 키를 폼 키로 되돌린 답. 고객에게 내부 키를 보이지 않는다.
    answers: list[Answer]


# ── 포털·요청 유형 관리 (내부) ─────────────────────────────────


class PortalService:
    """포털과 요청 유형 정의. 상담원·관리자가 쓴다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._portals = PortalRepository(session)
        self._types = RequestTypeRepository(session)

    # ── 포털 ────────────────────────────────────────────────────

    async def list_for_project(
        self, actor: Actor, project_id: UUID, *, include_archived: bool = False
    ) -> list[PortalView]:
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(project_id)
        )
        rows = await self._portals.list_for_projects(
            [project_id], include_archived=include_archived
        )
        return [
            PortalView(
                portal=row,
                request_type_count=len(await self._types.list_for_portal(row.id)),
            )
            for row in rows
        ]

    async def get(self, actor: Actor, portal_id: UUID) -> PortalView:
        portal = await self._require_portal(portal_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        return PortalView(
            portal=portal, request_type_count=len(await self._types.list_for_portal(portal.id))
        )

    async def create(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        name: str,
        slug: str,
        description: str | None,
        theme: dict[str, Any],
        is_public: bool,
    ) -> PortalView:
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(project_id)
        )
        project = await org.get_project(self._s, project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        if project.is_archived:
            raise ConflictError(
                "아카이브된 프로젝트에는 포털을 만들 수 없다.",
                code="desk.project_archived",
            )
        clean_slug = self._validate_slug(slug)
        if await self._portals.slug_exists(clean_slug):
            raise ConflictError("이미 쓰는 슬러그다.", code="desk.portal_slug_taken")

        portal = self._portals.add(
            Portal(
                project_id=project_id,
                name=name.strip(),
                slug=clean_slug,
                description=(description or "").strip() or None,
                theme=dict(theme),
                is_public=is_public,
            )
        )
        await self._s.flush()
        log.info("desk.portal.created", portal=str(portal.id), slug=clean_slug)
        return PortalView(portal=portal, request_type_count=0)

    async def update(
        self,
        actor: Actor,
        portal_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        theme: dict[str, Any] | None = None,
        is_public: bool | None = None,
    ) -> PortalView:
        portal = await self._require_portal(portal_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        if name is not None:
            portal.name = name.strip()
        if description is not None:
            portal.description = description.strip() or None
        if theme is not None:
            portal.theme = dict(theme)
        if is_public is not None:
            portal.is_public = is_public
        await self._s.flush()
        return PortalView(
            portal=portal, request_type_count=len(await self._types.list_for_portal(portal.id))
        )

    async def set_archived(self, actor: Actor, portal_id: UUID, *, archived: bool) -> PortalView:
        """포털을 접거나 되살린다. **지우지 않는다.**

        지우면 `request_type` 이 CASCADE 로 함께 사라지고, 그러면 이미 들어온
        티켓의 `request_type_id` 가 NULL 이 되어 "어떤 폼으로 들어왔나" 를
        영구히 잃는다. 접는 것으로 충분하다 — 고객에게는 사라지고, 기록은
        남는다.
        """
        portal = await self._require_portal(portal_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        portal.archived_at = utcnow() if archived else None
        await self._s.flush()
        return PortalView(
            portal=portal, request_type_count=len(await self._types.list_for_portal(portal.id))
        )

    # ── 요청 유형 ───────────────────────────────────────────────

    async def list_request_types(
        self, actor: Actor, portal_id: UUID
    ) -> tuple[Portal, list[RequestTypeView]]:
        portal = await self._require_portal(portal_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        rows = await self._types.list_for_portal(portal_id)
        views: list[RequestTypeView] = []
        for row in rows:
            name = await issues.get_issue_type(
                self._s, project_id=portal.project_id, issue_type_id=row.issue_type_id
            )
            views.append(
                RequestTypeView(
                    request_type=row,
                    # 유형이 프로젝트에서 빠졌으면 빈 문자열이 아니라 그대로
                    # 비워 둔다 — 화면이 "설정이 깨졌다" 를 말할 수 있어야 한다.
                    issue_type_name=name or "",
                    ticket_count=await self._types.ticket_count(row.id),
                )
            )
        return portal, views

    async def create_request_type(
        self,
        actor: Actor,
        portal_id: UUID,
        *,
        issue_type_id: UUID,
        name: str,
        description: str | None,
        icon: str | None,
        position: int,
        form_fields: list[dict[str, Any]],
        field_mapping: dict[str, str],
        is_enabled: bool,
    ) -> RequestTypeView:
        portal = await self._require_portal(portal_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        if portal.is_archived:
            raise ConflictError(
                "접힌 포털에는 요청 유형을 만들 수 없다.", code="desk.portal_archived"
            )
        type_name = await issues.get_issue_type(
            self._s, project_id=portal.project_id, issue_type_id=issue_type_id
        )
        if type_name is None:
            raise ValidationError(
                "이 프로젝트에서 쓸 수 없는 이슈 유형이다.",
                code="desk.issue_type_not_available",
            )
        clean_name = name.strip()
        if await self._types.name_exists(portal_id, clean_name):
            raise ConflictError(
                "같은 이름의 요청 유형이 있다.", code="desk.request_type_name_taken"
            )

        fields, mapping = await self._validate_form(
            project_id=portal.project_id,
            issue_type_id=issue_type_id,
            form_fields=form_fields,
            field_mapping=field_mapping,
        )
        row = self._types.add(
            RequestType(
                portal_id=portal_id,
                issue_type_id=issue_type_id,
                name=clean_name,
                description=(description or "").strip() or None,
                icon=(icon or "").strip() or None,
                position=position,
                form_schema={"fields": fields},
                field_mapping=mapping,
                is_enabled=is_enabled,
            )
        )
        await self._s.flush()
        log.info("desk.request_type.created", request_type=str(row.id), portal=str(portal_id))
        return RequestTypeView(request_type=row, issue_type_name=type_name, ticket_count=0)

    async def update_request_type(
        self,
        actor: Actor,
        request_type_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        position: int | None = None,
        form_fields: list[dict[str, Any]] | None = None,
        field_mapping: dict[str, str] | None = None,
        is_enabled: bool | None = None,
    ) -> RequestTypeView:
        row, portal = await self._require_request_type(request_type_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        if name is not None:
            clean_name = name.strip()
            if await self._types.name_exists(row.portal_id, clean_name, exclude_id=row.id):
                raise ConflictError(
                    "같은 이름의 요청 유형이 있다.", code="desk.request_type_name_taken"
                )
            row.name = clean_name
        if description is not None:
            row.description = description.strip() or None
        if icon is not None:
            row.icon = icon.strip() or None
        if position is not None:
            row.position = position
        if is_enabled is not None:
            row.is_enabled = is_enabled

        # 폼과 매핑은 **함께** 갈아 끼운다. 한쪽만 받으면 남은 쪽과 어긋난
        # 상태를 저장하게 된다 — 매핑 없는 필드나 필드 없는 매핑이 생긴다.
        if form_fields is not None or field_mapping is not None:
            current_fields = form_fields
            if current_fields is None:
                raw = row.form_schema.get("fields", [])
                current_fields = [dict(f) for f in raw] if isinstance(raw, list) else []
            fields, mapping = await self._validate_form(
                project_id=portal.project_id,
                issue_type_id=row.issue_type_id,
                form_fields=current_fields,
                field_mapping=(
                    field_mapping if field_mapping is not None else dict(row.field_mapping)
                ),
            )
            row.form_schema = {"fields": fields}
            row.field_mapping = mapping

        await self._s.flush()
        type_name = await issues.get_issue_type(
            self._s, project_id=portal.project_id, issue_type_id=row.issue_type_id
        )
        return RequestTypeView(
            request_type=row,
            issue_type_name=type_name or "",
            ticket_count=await self._types.ticket_count(row.id),
        )

    async def archive_request_type(
        self, actor: Actor, request_type_id: UUID, *, archived: bool
    ) -> RequestTypeView:
        row, portal = await self._require_request_type(request_type_id)
        await self._perms.require(
            self._s, actor, perms.PORTAL_MANAGE, scope=Scope.project(portal.project_id)
        )
        row.archived_at = utcnow() if archived else None
        await self._s.flush()
        type_name = await issues.get_issue_type(
            self._s, project_id=portal.project_id, issue_type_id=row.issue_type_id
        )
        return RequestTypeView(
            request_type=row,
            issue_type_name=type_name or "",
            ticket_count=await self._types.ticket_count(row.id),
        )

    # ── 검증 ────────────────────────────────────────────────────

    @staticmethod
    def _validate_slug(raw: str) -> str:
        slug = normalize_slug(raw)
        if not SLUG_PATTERN.match(slug):
            raise ValidationError(
                "슬러그는 소문자·숫자·하이픈만 쓰고 하이픈으로 시작·끝낼 수 없다.",
                code="desk.invalid_slug",
            )
        return slug

    async def _validate_form(
        self,
        *,
        project_id: UUID,
        issue_type_id: UUID,
        form_fields: list[dict[str, Any]],
        field_mapping: dict[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """폼과 매핑이 서로, 그리고 커스텀 필드 정의와 맞는지 본다.

        여기서 거절하지 않으면 **저장은 되고 제출이 실패하는** 폼이 생긴다.
        고객은 폼을 다 채우고 마지막에 실패를 본다 — 가장 나쁜 자리에서
        드러나는 결함이다. 그래서 규칙을 저장 시점에 전부 본다.
        """
        keys = [str(f.get("key", "")).strip().lower() for f in form_fields]
        if len(set(keys)) != len(keys):
            raise ValidationError("폼 필드 키가 중복된다.", code="desk.duplicate_form_key")
        bad = [k for k in keys if not FORM_KEY_PATTERN.match(k)]
        if bad:
            raise ValidationError(
                "폼 필드 키는 소문자로 시작하고 소문자·숫자·밑줄만 쓴다.",
                code="desk.invalid_form_key",
                details={"keys": sorted(bad)},
            )
        # 요약이 없으면 큐에서 훑을 수 없는 티켓이 된다. 서버가 대신 지어
        # 넣는 것은 더 나쁘다 — 목록이 전부 같은 글자가 된다.
        if "summary" not in keys:
            raise ValidationError(
                "폼에는 요약(summary) 필드가 있어야 한다.",
                code="desk.form_needs_summary",
            )

        custom_keys = [k for k in keys if k not in RESERVED_FORM_KEYS]
        mapping = {k: str(v).strip().lower() for k, v in field_mapping.items() if str(v).strip()}
        reserved_mapped = sorted(set(mapping) & set(RESERVED_FORM_KEYS))
        if reserved_mapped:
            raise ValidationError(
                "예약 필드는 이슈 자신의 칸으로 가므로 매핑하지 않는다.",
                code="desk.reserved_key_mapped",
                details={"keys": reserved_mapped},
            )
        unmapped = sorted(set(custom_keys) - set(mapping))
        if unmapped:
            # 갈 곳 없는 답은 버려지는 답이고, 고객은 그것을 알 수 없다.
            raise ValidationError(
                "이 폼 필드는 커스텀 필드로 갈 곳이 없다.",
                code="desk.form_field_unmapped",
                details={"keys": unmapped},
            )
        dangling = sorted(set(mapping) - set(custom_keys))
        if dangling:
            raise ValidationError(
                "폼에 없는 필드의 매핑이 남아 있다.",
                code="desk.mapping_without_field",
                details={"keys": dangling},
            )
        targets = list(mapping.values())
        if len(set(targets)) != len(targets):
            raise ValidationError(
                "두 폼 필드가 같은 커스텀 필드를 가리킨다.",
                code="desk.duplicate_mapping_target",
            )

        definitions = await issues.get_field_definitions(
            self._s, project_id=project_id, issue_type_id=issue_type_id
        )
        by_key = {d.key: d for d in definitions}
        missing = sorted(set(targets) - set(by_key))
        if missing:
            raise ValidationError(
                "이 프로젝트·유형에 없는 커스텀 필드다.",
                code="desk.unknown_field_definition",
                details={"keys": missing},
            )
        forbidden = sorted(key for key in targets if by_key[key].kind in FORBIDDEN_FORM_KINDS)
        if forbidden:
            raise ValidationError(
                "이 종류는 선택지가 내부 데이터라 포털 폼에 올릴 수 없다.",
                code="desk.field_kind_not_on_forms",
                details={"keys": forbidden},
            )
        # 정의가 필수인데 폼에 없으면 제출이 **언제나** 실패한다.
        required_missing = sorted(
            d.key for d in definitions if d.is_required and d.key not in set(targets)
        )
        if required_missing:
            raise ValidationError(
                "필수 커스텀 필드가 폼에 없다. 이대로면 제출이 항상 실패한다.",
                code="desk.required_field_missing_from_form",
                details={"keys": required_missing},
            )

        normalized: list[dict[str, Any]] = []
        for raw, key in zip(form_fields, keys, strict=True):
            label = str(raw.get("label", "")).strip()
            if not label:
                raise ValidationError(
                    "폼 필드에는 라벨이 있어야 한다.",
                    code="desk.form_field_needs_label",
                    details={"key": key},
                )
            help_text = str(raw.get("help") or "").strip() or None
            normalized.append(
                {
                    "key": key,
                    "label": label,
                    "help": help_text,
                    "required": bool(raw.get("required")),
                }
            )
        return normalized, mapping

    # ── 조회 헬퍼 ───────────────────────────────────────────────

    async def _require_portal(self, portal_id: UUID) -> Portal:
        portal = await self._portals.get(portal_id)
        if portal is None:
            raise NotFoundError("포털을 찾을 수 없다.")
        return portal

    async def _require_request_type(self, request_type_id: UUID) -> tuple[RequestType, Portal]:
        row = await self._types.get(request_type_id)
        if row is None:
            raise NotFoundError("요청 유형을 찾을 수 없다.")
        return row, await self._require_portal(row.portal_id)


# ── 고객 조직 ───────────────────────────────────────────────────


class CustomerOrgService:
    """고객 조직과 소속. 소속이 티켓 가시성을 정한다 (auth.md 5절)."""

    def __init__(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        self._s = session
        self._perms = permissions
        # 초대는 계정을 만드는 일이고, identity 의 초대는 설정(비밀번호 정책
        # 파라미터 등)을 든다. 그래서 여기까지 내려온다.
        self._settings = settings
        self._orgs = CustomerOrganizationRepository(session)
        self._members = CustomerMembershipRepository(session)

    async def list_all(
        self, actor: Actor, request: PageRequest, *, query: str | None = None
    ) -> Page[CustomerOrgView]:
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        page = await self._orgs.list_page(request, query=query)
        counts = await self._orgs.member_counts([row.id for row in page.items])
        return Page(
            items=[
                CustomerOrgView(organization=row, member_count=counts.get(row.id, 0))
                for row in page.items
            ],
            next_cursor=page.next_cursor,
        )

    async def create(
        self, actor: Actor, *, name: str, domains: list[str], note: str | None
    ) -> CustomerOrgView:
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        clean_name = name.strip()
        if await self._orgs.name_exists(clean_name):
            raise ConflictError(
                "같은 이름의 고객 조직이 있다.", code="desk.customer_org_name_taken"
            )
        row = self._orgs.add(
            CustomerOrganization(
                name=clean_name,
                domains=self._clean_domains(domains),
                note=(note or "").strip() or None,
            )
        )
        await self._s.flush()
        identity.record_audit(
            self._s,
            action=AUDIT_CUSTOMER_ORG_CREATED,
            actor_id=actor.user_id,
            target_type="customer_organization",
            target_id=row.id,
            metadata={"name": clean_name},
        )
        return CustomerOrgView(organization=row, member_count=0)

    async def update(
        self,
        actor: Actor,
        organization_id: UUID,
        *,
        name: str | None = None,
        domains: list[str] | None = None,
        note: str | None = None,
    ) -> CustomerOrgView:
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        row = await self._require_org(organization_id)
        if name is not None:
            clean_name = name.strip()
            if await self._orgs.name_exists(clean_name, exclude_id=row.id):
                raise ConflictError(
                    "같은 이름의 고객 조직이 있다.", code="desk.customer_org_name_taken"
                )
            row.name = clean_name
        if domains is not None:
            row.domains = self._clean_domains(domains)
        if note is not None:
            row.note = note.strip() or None
        await self._s.flush()
        counts = await self._orgs.member_counts([row.id])
        return CustomerOrgView(organization=row, member_count=counts.get(row.id, 0))

    async def set_archived(
        self, actor: Actor, organization_id: UUID, *, archived: bool
    ) -> CustomerOrgView:
        """조직을 접거나 되살린다. **소속은 건드리지 않는다.**

        접는 것은 "새 고객을 이 도메인으로 자동 배정하지 않는다" 는 뜻이다.
        소속을 끊어 버리면 이미 들어온 티켓의 조직 가시성이 사라져, 같은
        기관의 동료가 보던 티켓이 조용히 안 보이게 된다.
        """
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        row = await self._require_org(organization_id)
        row.archived_at = utcnow() if archived else None
        await self._s.flush()
        counts = await self._orgs.member_counts([row.id])
        return CustomerOrgView(organization=row, member_count=counts.get(row.id, 0))

    async def list_members(self, actor: Actor, organization_id: UUID) -> list[identity.UserRef]:
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        await self._require_org(organization_id)
        user_ids = await self._members.user_ids_in(organization_id)
        users = await identity.get_users(self._s, user_ids)
        return sorted(users.values(), key=lambda u: u.display_name)

    async def add_member(
        self, actor: Actor, organization_id: UUID, *, user_id: UUID
    ) -> identity.UserRef:
        """고객을 조직에 넣는다. **내부 계정은 넣지 않는다.**

        내부 사용자를 고객 조직에 넣으면 조직 가시성이 그 사람에게도 붙고,
        내부 계정은 포털 밖도 보므로 "고객 조직 소속" 이라는 개념이 흐려진다.
        """
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        await self._require_org(organization_id)
        user = await identity.get_user(self._s, user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없다.")
        if not user.is_customer:
            raise ConflictError("내부 계정은 고객 조직에 넣을 수 없다.", code="desk.not_a_customer")
        await self._members.set(user_id=user_id, organization_id=organization_id)
        await self._s.flush()
        identity.record_audit(
            self._s,
            action=AUDIT_CUSTOMER_MEMBERSHIP_CHANGED,
            actor_id=actor.user_id,
            target_type="user",
            target_id=user_id,
            metadata={"organization_id": str(organization_id)},
        )
        return user

    async def remove_member(self, actor: Actor, organization_id: UUID, *, user_id: UUID) -> bool:
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        await self._require_org(organization_id)
        current = await self._members.organization_of(user_id)
        if current != organization_id:
            # 다른 조직의 소속을 여기서 끊게 하면 실수로 남의 조직을 비운다.
            raise ConflictError("이 조직의 소속이 아니다.", code="desk.not_a_member")
        removed = await self._members.clear(user_id)
        await self._s.flush()
        if removed:
            identity.record_audit(
                self._s,
                action=AUDIT_CUSTOMER_MEMBERSHIP_CHANGED,
                actor_id=actor.user_id,
                target_type="user",
                target_id=user_id,
                metadata={"organization_id": None},
            )
        return removed

    async def invite_customer(
        self, actor: Actor, organization_id: UUID, *, email: str, display_name: str
    ) -> identity.UserRef:
        """고객을 초대하고 **바로 이 조직에 넣는다.**

        한 트랜잭션이다. 초대만 하고 소속을 워커에 맡기면(이벤트) 아웃박스가
        훑기 전까지 소속 없는 고객이 존재하고, 그 사이에 낸 티켓은 조직
        가시성을 잃는다 — 15초짜리 창이지만 조용히 잘못되는 종류다.

        조직이 경로에 있으므로 도메인 추측이 필요 없다. 도메인은 화면이
        기본값을 **제안**하는 데만 쓴다: 사람이 확인한 소속이라야 ACL 이
        사람의 결정으로 남는다.
        """
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        await self._require_org(organization_id)
        clean_email = email.strip().lower()
        if not EMAIL_PATTERN.match(clean_email):
            raise ValidationError("이메일 모양이 아니다.", code="desk.invalid_email")
        user = await identity.invite_customer(
            self._s,
            self._settings,
            email=clean_email,
            display_name=display_name.strip(),
            invited_by=actor.user_id,
        )
        await self._members.set(user_id=user.id, organization_id=organization_id)
        await self._s.flush()
        identity.record_audit(
            self._s,
            action=AUDIT_CUSTOMER_MEMBERSHIP_CHANGED,
            actor_id=actor.user_id,
            target_type="user",
            target_id=user.id,
            metadata={"organization_id": str(organization_id), "invited": True},
        )
        return user

    async def suggest_organization(self, actor: Actor, email: str) -> UUID | None:
        """이 주소의 도메인을 주장하는 조직. 화면의 **기본값 제안**이다."""
        await self._perms.require(self._s, actor, perms.CUSTOMER_MANAGE, scope=Scope.global_())
        row = await self._orgs.find_by_domain(email.strip().lower().rpartition("@")[2])
        return row.id if row else None

    @staticmethod
    def _clean_domains(raw: list[str]) -> list[str]:
        seen: list[str] = []
        for item in raw:
            domain = normalize_domain(item)
            if not domain or domain in seen:
                continue
            if "." not in domain or " " in domain:
                raise ValidationError(
                    "도메인 모양이 아니다.", code="desk.invalid_domain", details={"domain": domain}
                )
            seen.append(domain)
        return seen

    async def _require_org(self, organization_id: UUID) -> CustomerOrganization:
        row = await self._orgs.get(organization_id)
        if row is None:
            raise NotFoundError("고객 조직을 찾을 수 없다.")
        return row


#: 감사 로그 행동 이름. 정본은 identity 쪽이고 여기서는 상수만 쓴다.
AUDIT_CUSTOMER_ORG_CREATED = "desk.customer_org.created"
AUDIT_CUSTOMER_MEMBERSHIP_CHANGED = "desk.customer_membership.changed"


# ── 상담원이 보는 티켓 ─────────────────────────────────────────


class AgentTicketService:
    """상담원 화면이 쓰는 데스크 정보. 큐 작업 권한을 요구한다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._tickets = TicketRepository(session)
        self._types = RequestTypeRepository(session)
        self._portals = PortalRepository(session)
        self._orgs = CustomerOrganizationRepository(session)

    async def get(self, actor: Actor, issue_id: UUID) -> AgentTicketView | None:
        """이 이슈의 데스크 정보. **티켓이 아니면 `None` 이다 — 오류가 아니다.**

        처음에는 404 로 답했다. 화면이 "이슈인가 티켓인가" 를 이 응답으로
        가르므로 그게 자연스러워 보였고, 빈 값을 200 으로 주면 화면이 모든
        이슈에 고객 회신 손잡이를 그릴 수 있다는 것이 이유였다.

        **그런데 상담원이 평범한 이슈를 열 때마다 콘솔에 404 가 찍혔다.**
        이슈 상세는 이 제품에서 가장 많이 열리는 화면이고, 거기서 매번
        오류가 나면 사람은 콘솔을 안 보게 된다 — 그러면 진짜 오류가 가장
        오래 살아남는다. E2E 의 `consoleErrors` 픽스처가 이 판단을 이미
        코드로 갖고 있어서, 이슈 스펙 넷이 붉어져 드러났다. 그 픽스처의
        예외 목록에 이 경로를 더하는 것으로 고치지 않는다: 예외 목록은
        어쩔 수 없는 것(익명의 `/auth/me` 401)을 담는 자리이고, 내가 고른
        설계를 담는 자리가 아니다.

        빈 값을 주면서도 구조로 막을 수 있다. 응답의 `ticket` 이
        **널 가능**이므로 화면은 확인 없이 `TicketFacts` 를 그릴 수 없다 —
        `tsc` 가 거절한다. 404 가 사 주던 보장을 타입이 대신 산다.

        `desk.queue.work` 가 없는 사람에게도 `None` 이다. 그 사람에게 이
        화면이 할 일은 "데스크 정보를 그리지 않는다" 로 똑같고, 403 으로
        답하면 그것도 콘솔 오류가 된다 — 그리고 이슈를 볼 수는 있는 사람에게
        "이건 티켓인데 너는 볼 수 없다" 를 알려 줄 이유가 없다.
        """
        ticket = await self._tickets.get(issue_id)
        if ticket is None:
            return None
        issue = await issues.get_issue(self._s, issue_id)
        if issue is None:
            # **데이터베이스가 허용하지 않는 상태다.** `ticket_ext.issue_id` 는
            # `issue.id` 를 가리키는 PK 겸 FK(`ON DELETE CASCADE`)이므로 티켓이
            # 있으면 이슈도 있다. 타입을 좁히려고 남긴 줄이고, 여기 왔다면
            # 그건 제약이 깨졌다는 뜻이라 조용히 넘기지 않는다.
            raise NotFoundError("요청을 찾을 수 없다.")
        if not await self._perms.has(
            self._s, actor, perms.QUEUE_WORK, scope=Scope.project(issue.project_id)
        ):
            return None

        request_type = (
            await self._types.get(ticket.request_type_id) if ticket.request_type_id else None
        )
        portal = await self._portals.get(request_type.portal_id) if request_type else None
        organization = (
            await self._orgs.get(ticket.organization_id) if ticket.organization_id else None
        )
        return AgentTicketView(
            ticket=ticket,
            request_type_name=request_type.name if request_type else None,
            portal_slug=portal.slug if portal else None,
            requester=await self._requester(ticket),
            organization_name=organization.name if organization else None,
            sla=await self._sla_standings(issue_id),
        )

    async def _sla_standings(self, issue_id: UUID) -> list[SlaStanding]:
        """이 티켓의 SLA 잔여 시간.

        달력이 망가진 정책은 **건너뛴다.** 티켓 화면 전체가 그것 때문에
        실패하면, SLA 하나가 잘못 저장된 것이 티켓을 못 여는 일이 된다.
        """
        rows = await self._s.execute(
            select(SlaClock, SlaPolicy, BusinessCalendarRow)
            .join(SlaPolicy, SlaPolicy.id == SlaClock.policy_id)
            .join(BusinessCalendarRow, BusinessCalendarRow.id == SlaPolicy.calendar_id)
            .where(SlaClock.issue_id == issue_id)
            .order_by(SlaPolicy.name)
        )
        out: list[SlaStanding] = []
        for clock, policy, calendar_row in rows.all():
            try:
                calendar = parse_calendar(
                    timezone=calendar_row.timezone,
                    working_hours=calendar_row.working_hours,
                    holidays=list(calendar_row.holidays),
                )
            except CalendarError:
                log.error("sla.broken_calendar_on_ticket", calendar_id=str(calendar_row.id))
                continue
            left = sla_remaining(
                calendar,
                target_at=sla_utc(clock.target_at),
                now=utcnow(),
                paused_at=sla_utc(clock.paused_at) if clock.paused_at else None,
                completed_at=sla_utc(clock.completed_at) if clock.completed_at else None,
            )
            out.append(
                SlaStanding(
                    policy_name=policy.name,
                    metric=policy.metric,
                    target_at=clock.target_at,
                    remaining_seconds=left.seconds,
                    breached=left.breached,
                    paused=left.paused,
                    completed=left.completed,
                )
            )
        return out

    async def _requester(self, ticket: TicketExt) -> Requester | None:
        if ticket.reporter_customer_id is not None:
            user = await identity.get_user(self._s, ticket.reporter_customer_id)
            if user is not None:
                return Requester(
                    user_id=user.id,
                    display_name=user.display_name,
                    email=user.email,
                    # 계정 주소는 초대 메일을 받아 비밀번호를 정한 주소다.
                    verified=True,
                )
        if ticket.guest_email is not None:
            return Requester(
                user_id=None,
                display_name=ticket.guest_name or "",
                email=ticket.guest_email,
                verified=False,
            )
        return None


# ── 포털 (고객이 쓰는 쪽) ──────────────────────────────────────


class CustomerPortalService:
    """고객과 게스트가 쓰는 표면. 권한은 역할이 아니라 **포털**이 정한다.

    고객 계정에는 프로젝트 역할이 없다. 그래서 여기서는 `PermissionService`
    로 묻지 않고 "이 포털이 이 요청 유형을 열어 두었는가" 와 "이 티켓이 내
    것 또는 내 조직 것인가" 두 가지만 본다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._portals = PortalRepository(session)
        self._types = RequestTypeRepository(session)
        self._members = CustomerMembershipRepository(session)
        self._tickets = TicketRepository(session)
        self._orgs = CustomerOrganizationRepository(session)

    async def list_open_portals(self) -> list[Portal]:
        """접히지 않은 포털 전부. **로그인한 사람에게만** 낸다.

        왜 필요한가: 초대를 받아 비밀번호를 정한 고객이 앱 뿌리로 들어오면
        "어느 창구로 보낼지" 를 알 수 없다 — 서버는 고객과 포털을 묶지 않고
        (한 고객이 여러 창구를 쓸 수 있다), 그 브라우저에는 기억해 둔 창구도
        없다. 그러면 "포털을 쓰세요" 라고만 적힌 막다른 화면이 된다.

        익명에게는 열지 않는다. 창구 이름은 고객에게 보이는 값이지만, 목록을
        통째로 열어 두면 설치된 창구를 아무나 훑을 수 있다.

        보통 하나뿐이다. 여럿이면 화면이 고르게 한다.
        """
        return await self._portals.list_for_projects_all()

    async def info(self, slug: str) -> Portal:
        """포털의 겉모습. 로그인 없이 열린다.

        접힌 포털은 없는 것처럼 다룬다 — 접었는데 여전히 응답하면 접은 것이
        아니다.
        """
        portal = await self._portals.get_by_slug(slug)
        if portal is None or portal.is_archived:
            raise NotFoundError("포털을 찾을 수 없다.")
        return portal

    async def list_forms(self, slug: str) -> tuple[Portal, list[RequestType]]:
        portal = await self.info(slug)
        return portal, await self._types.list_for_portal(portal.id, enabled_only=True)

    async def get_form(self, slug: str, request_type_id: UUID) -> PortalForm:
        portal = await self.info(slug)
        row = await self._require_enabled_type(portal, request_type_id)
        definitions = await issues.get_field_definitions(
            self._s, project_id=portal.project_id, issue_type_id=row.issue_type_id
        )
        by_key = {d.key: d for d in definitions}
        fields: list[FormField] = []
        raw_fields = row.form_schema.get("fields", [])
        for spec in raw_fields if isinstance(raw_fields, list) else []:
            key = str(spec.get("key", ""))
            if key in RESERVED_KINDS:
                fields.append(
                    FormField(
                        key=key,
                        label=str(spec.get("label", "")),
                        help=spec.get("help"),
                        # 요약은 이슈에 반드시 있어야 하므로 폼 설정과 무관하게
                        # 필수다. 설정으로 끌 수 있게 두면 빈 제목의 티켓이 온다.
                        required=key == "summary" or bool(spec.get("required")),
                        kind=RESERVED_KINDS[key],
                        config={},
                    )
                )
                continue
            target = row.field_mapping.get(key)
            definition = by_key.get(target) if target else None
            if definition is None:
                # 정의가 사라진 필드는 **조용히 뺀다.** 여기서 예외를 던지면
                # 관리자의 실수 하나가 고객의 폼을 통째로 막는다. 관리 화면
                # 쪽에서 저장할 때 이미 거절하므로, 여기 오는 것은 나중에
                # 정의가 지워진 경우다.
                log.warning(
                    "desk.form.field_definition_missing",
                    request_type=str(row.id),
                    form_key=key,
                    field_key=target,
                )
                continue
            fields.append(
                FormField(
                    key=key,
                    label=str(spec.get("label", "")),
                    help=spec.get("help"),
                    required=bool(spec.get("required")) or definition.is_required,
                    kind=definition.kind,
                    config=dict(definition.config),
                )
            )
        return PortalForm(request_type=row, fields=fields)

    async def submit(
        self, actor: Actor, slug: str, *, request_type_id: UUID, answers: dict[str, Any]
    ) -> TicketView:
        """로그인한 고객의 제출."""
        portal = await self.info(slug)
        form = await self.get_form(slug, request_type_id)
        summary, description, custom = self._split_answers(form, answers)
        organization_id = await self._members.organization_of(actor.user_id)

        issue = await issues.create_ticket(
            self._s,
            self._perms,
            actor,
            project_id=portal.project_id,
            issue_type_id=form.request_type.issue_type_id,
            summary=summary,
            description=description,
            custom_fields=custom,
        )
        ticket = self._tickets.add(
            TicketExt(
                issue_id=issue.id,
                request_type_id=form.request_type.id,
                reporter_customer_id=actor.user_id,
                channel="portal",
                organization_id=organization_id,
            )
        )
        await self._s.flush()
        publish(
            self._s,
            TicketSubmitted(
                aggregate_id=issue.id,
                project_id=portal.project_id,
                issue_key=issue.key,
                request_type_id=form.request_type.id,
                reporter_customer_id=actor.user_id,
                organization_id=organization_id,
            ),
        )
        log.info("desk.ticket.created", issue=issue.key, channel="portal")
        return self._ticket_view(ticket, issue, form.request_type)

    async def submit_as_guest(
        self, slug: str, *, request_type_id: UUID, email: str, name: str, answers: dict[str, Any]
    ) -> TicketView:
        """로그인 없는 제출. 포털이 `is_public` 일 때만.

        **주소를 검증하지 않는다.** 아무 주소나 적을 수 있다. 그래서 이 주소로
        보내는 것은 "요청이 접수됐다" 와 추적 링크뿐이고 본문을 되돌려 보내지
        않는다 — 남의 주소로 욕설을 제출하면 그 사람에게 욕설이 배달되기
        때문이다. 계정이 이미 있는 주소라도 자동으로 그 계정에 붙이지 않는다:
        증명 없이 남의 이름으로 티켓을 만드는 일이 된다.
        """
        portal = await self.info(slug)
        if not portal.is_public:
            raise PermissionDeniedError(
                "이 포털은 로그인해야 요청할 수 있다.", code="desk.guest_not_allowed"
            )
        clean_email = email.strip().lower()
        if not EMAIL_PATTERN.match(clean_email):
            raise ValidationError("이메일 모양이 아니다.", code="desk.invalid_email")

        form = await self.get_form(slug, request_type_id)
        summary, description, custom = self._split_answers(form, answers)
        # 도메인으로 조직을 붙인다. 소속 행을 만들지는 않는다 — 계정이 없으니
        # 붙일 사람이 없고, 나중에 계정이 생기면 그때 소속이 정해진다.
        organization = await self._orgs.find_by_domain(clean_email.rpartition("@")[2])

        issue = await issues.create_ticket(
            self._s,
            self._perms,
            guest_actor(clean_email),
            project_id=portal.project_id,
            issue_type_id=form.request_type.issue_type_id,
            summary=summary,
            description=description,
            custom_fields=custom,
            anonymous=True,
        )
        ticket = self._tickets.add(
            TicketExt(
                issue_id=issue.id,
                request_type_id=form.request_type.id,
                reporter_customer_id=None,
                guest_email=clean_email,
                guest_name=name.strip(),
                channel="portal",
                organization_id=organization.id if organization else None,
            )
        )
        await self._s.flush()
        publish(
            self._s,
            TicketSubmitted(
                aggregate_id=issue.id,
                project_id=portal.project_id,
                issue_key=issue.key,
                request_type_id=form.request_type.id,
                guest_email=clean_email,
                organization_id=organization.id if organization else None,
            ),
        )
        log.info("desk.ticket.created", issue=issue.key, channel="portal", guest=True)
        return self._ticket_view(ticket, issue, form.request_type)

    async def list_my_tickets(
        self, actor: Actor, slug: str, request: PageRequest
    ) -> Page[TicketView]:
        """내 요청 목록. 내 것 + 내 조직 것 (auth.md 5절)."""
        portal = await self.info(slug)
        organization_id = await self._members.organization_of(actor.user_id)
        page = await self._tickets.visible_to_customer(
            request,
            user_id=actor.user_id,
            organization_id=organization_id,
            portal_id=portal.id,
        )
        return Page(
            items=await self._views_for(page.items),
            next_cursor=page.next_cursor,
        )

    async def get_my_ticket(self, actor: Actor, slug: str, issue_id: UUID) -> TicketView:
        await self.info(slug)
        ticket = await self._tickets.get(issue_id)
        if ticket is None:
            raise NotFoundError("요청을 찾을 수 없다.")
        organization_id = await self._members.organization_of(actor.user_id)
        mine = ticket.reporter_customer_id == actor.user_id
        same_org = organization_id is not None and ticket.organization_id == organization_id
        if not (mine or same_org):
            # 404 다. 남의 티켓이 **있다는 사실**도 알려 주지 않는다.
            raise NotFoundError("요청을 찾을 수 없다.")
        views = await self._views_for([ticket])
        if not views:
            raise NotFoundError("요청을 찾을 수 없다.")
        return views[0]

    # ── 대화 (C7) ───────────────────────────────────────────────

    async def list_replies(
        self, actor: Actor, slug: str, issue_id: UUID
    ) -> list[issues.PublicComment]:
        """이 요청의 대화. **내부 노트는 여기 오지 않는다.**

        보장이 두 겹이다: 먼저 이 티켓이 이 고객의 것인지 확인하고(아니면
        404), 그 다음 공개 코멘트만 읽는 계약을 쓴다 — 그 계약에는
        `include_internal` 매개변수가 아예 없다.
        """
        await self.get_my_ticket(actor, slug, issue_id)
        return await issues.list_public_comments(self._s, issue_id)

    async def reply(
        self, actor: Actor, slug: str, issue_id: UUID, body: str
    ) -> issues.PublicComment:
        """고객의 회신. 자기 티켓(또는 자기 조직 티켓)에만 쓸 수 있다."""
        await self.get_my_ticket(actor, slug, issue_id)
        return await issues.add_public_comment(self._s, actor, issue_id, body)

    # ── 내부 ────────────────────────────────────────────────────

    async def _views_for(self, tickets: list[TicketExt]) -> list[TicketView]:
        if not tickets:
            return []
        found = await issues.get_tickets(self._s, [t.issue_id for t in tickets])
        types: dict[UUID, RequestType] = {}
        for ticket in tickets:
            if ticket.request_type_id and ticket.request_type_id not in types:
                row = await self._types.get(ticket.request_type_id)
                if row is not None:
                    types[ticket.request_type_id] = row
        views: list[TicketView] = []
        for ticket in tickets:
            issue = found.get(ticket.issue_id)
            if issue is None:
                # 이슈가 지워졌는데 확장이 남는 일은 CASCADE 때문에 없어야
                # 한다. 그래도 목록이 500 으로 죽는 것보다 조용히 빠지는 쪽이
                # 낫다 — 로그로 남긴다.
                log.warning("desk.ticket.issue_missing", issue_id=str(ticket.issue_id))
                continue
            row = types.get(ticket.request_type_id) if ticket.request_type_id else None
            views.append(self._ticket_view(ticket, issue, row))
        return views

    @staticmethod
    def _ticket_view(
        ticket: TicketExt, issue: issues.TicketIssue, request_type: RequestType | None
    ) -> TicketView:
        # 답을 폼 키로 되돌린다. 고객에게 커스텀 필드 키를 보여 주면 내부
        # 스키마가 새고, 폼 키가 바뀌면 화면이 답을 못 찾는다.
        #
        # **폼에 적힌 순서대로** 낸다. 매핑(dict)의 순서는 편집할 때마다
        # 바뀌므로, 그것을 따르면 같은 티켓이 볼 때마다 다르게 정렬된다.
        answers: list[Answer] = []
        if request_type is not None:
            labels = _form_labels(request_type)
            order = list(labels) or list(request_type.field_mapping)
            for form_key in order:
                field_key = request_type.field_mapping.get(form_key)
                if field_key is None or field_key not in issue.custom_fields:
                    continue
                answers.append(
                    Answer(
                        key=form_key,
                        label=labels.get(form_key, form_key),
                        value=issue.custom_fields[field_key],
                    )
                )
        return TicketView(
            ticket=ticket,
            issue=issue,
            request_type_name=request_type.name if request_type else None,
            answers=answers,
        )

    async def _require_enabled_type(self, portal: Portal, request_type_id: UUID) -> RequestType:
        row = await self._types.get(request_type_id)
        if row is None or row.portal_id != portal.id or row.is_archived or not row.is_enabled:
            raise NotFoundError("요청 유형을 찾을 수 없다.")
        return row

    @staticmethod
    def _split_answers(
        form: PortalForm, answers: dict[str, Any]
    ) -> tuple[str, str | None, dict[str, Any]]:
        """폼 키로 온 답을 요약·본문·커스텀 필드로 나눈다.

        모르는 키는 **거절한다.** 조용히 버리면 고객은 적은 내용이 사라진
        것을 모른다.
        """
        known = {f.key for f in form.fields}
        unknown = sorted(set(answers) - known)
        if unknown:
            raise ValidationError(
                "이 폼에 없는 항목이다.",
                code="desk.unknown_answer",
                details={"keys": unknown},
            )
        for field_spec in form.fields:
            value = answers.get(field_spec.key)
            empty = value is None or (isinstance(value, str) and not value.strip())
            if field_spec.required and empty:
                raise ValidationError(
                    "필수 항목이 비어 있다.",
                    code="desk.answer_required",
                    details={"key": field_spec.key},
                )

        summary = str(answers.get("summary", "")).strip()
        if not summary:
            raise ValidationError(
                "요약이 비어 있다.", code="desk.answer_required", details={"key": "summary"}
            )
        raw_description = answers.get("description")
        description = str(raw_description).strip() or None if raw_description else None

        mapping = form.request_type.field_mapping
        custom: dict[str, Any] = {}
        for field_spec in form.fields:
            if field_spec.key in RESERVED_KINDS:
                continue
            if field_spec.key not in answers:
                continue
            value = answers[field_spec.key]
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            custom[mapping[field_spec.key]] = value
        return summary, description, custom


def _form_labels(request_type: RequestType) -> dict[str, str]:
    """폼 키 → 라벨. 순서를 지킨 dict 다 (파이썬 dict 는 삽입 순서를 지킨다)."""
    raw = request_type.form_schema.get("fields", [])
    if not isinstance(raw, list):
        return {}
    labels: dict[str, str] = {}
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key", ""))
        if key:
            labels[key] = str(spec.get("label", "")) or key
    return labels


def guest_actor(email: str) -> Actor:
    """게스트용 액터. **사용자 id 가 없다.**

    `Actor.user_id` 는 non-null 이므로 nil UUID 를 쓴다. 이슈의
    `reporter_id` 는 `anonymous=True` 로 NULL 이 되므로 FK 를 건드리지
    않는다. 이벤트의 `actor_id` 에는 이 nil 이 남는데, 그건 맞는 값이다 —
    "우리 사용자 중 누구도 아니다" 라는 뜻이고, 그래서 알림에서 "행위자
    자신은 빼기" 규칙이 아무도 빼지 않는다. 게스트 요청은 담당자 전원이
    받아야 하는 것이 맞다.

    `is_customer=True` 인 것도 일부러다. 혹시 이 액터가 권한 검사에 닿으면
    통과가 아니라 거절 쪽으로 넘어져야 한다.
    """
    return Actor(user_id=_NIL_UUID, email=email, is_customer=True, is_active=True)


_NIL_UUID = UUID(int=0)


# ── 큐 (C3) ────────────────────────────────────────────────────

#: 큐 이름·정형 응답 이름의 길이 상한. 모델의 컬럼과 같아야 한다 —
#: 넘치면 DB 가 500 으로 거절하고, 사람은 무엇이 문제인지 못 본다.
NAME_MAX = 120
#: 단축어. `/` 로 부르는 자리이므로 공백을 두지 않는다.
SHORTCUT_PATTERN = re.compile(r"^[A-Za-z0-9가-힣_-]{1,40}$")


@dataclass(frozen=True, slots=True)
class QueueView:
    queue: Queue


class QueueService:
    """큐 정의와 실행.

    정의를 고치는 것은 `desk.queue.manage`, 목록을 보는 것은
    `desk.queue.work` 다. 그리고 큐가 내주는 티켓은 실행자의 `issue.view`
    ACL 을 그대로 탄다 — **큐가 권한을 넓히지 않는다.**
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._queues = QueueRepository(session)

    # ── 정의 ────────────────────────────────────────────────────

    async def create(
        self, actor: Actor, *, project_id: UUID, name: str, iql: str, position: int
    ) -> QueueView:
        await self._perms.require(
            self._s, actor, perms.QUEUE_MANAGE, scope=Scope.project(project_id)
        )
        clean_name = await self._validated_name(project_id, name)
        clean_iql = await self._validated_iql(actor, iql)
        queue = self._queues.add(
            Queue(project_id=project_id, name=clean_name, iql=clean_iql, position=max(position, 0))
        )
        await self._s.flush()
        return QueueView(queue=queue)

    async def update(
        self,
        actor: Actor,
        queue_id: UUID,
        *,
        name: str | None = None,
        iql: str | None = None,
        position: int | None = None,
    ) -> QueueView:
        queue = await self._require_queue(actor, queue_id, perms.QUEUE_MANAGE)
        if name is not None:
            queue.name = await self._validated_name(queue.project_id, name, exclude=queue.id)
        if iql is not None:
            queue.iql = await self._validated_iql(actor, iql)
        if position is not None:
            queue.position = max(position, 0)
        await self._s.flush()
        return QueueView(queue=queue)

    async def delete(self, actor: Actor, queue_id: UUID) -> None:
        """보관한다. 지우지 않는다 — 큐 이름이 감사 로그와 링크에 남아 있다."""
        queue = await self._require_queue(actor, queue_id, perms.QUEUE_MANAGE)
        queue.archived_at = utcnow()
        await self._s.flush()

    async def list_for(self, actor: Actor, project_id: UUID) -> list[Queue]:
        """이 프로젝트의 큐. 일하는 권한으로 본다."""
        await self._perms.require(self._s, actor, perms.QUEUE_WORK, scope=Scope.project(project_id))
        return await self._queues.list_for_project(project_id)

    # ── 실행 ────────────────────────────────────────────────────

    async def run(
        self, actor: Actor, queue_id: UUID, request: PageRequest
    ) -> Page[issues.TicketRow]:
        """큐를 돌려 티켓 목록을 낸다. **티켓만 나온다.**

        조건을 밖에서 걸러내지 않고 한 질의에 넣는다 — 밖에서 걸러내면
        50개를 읽어 3개가 남고, 다음 페이지가 어디인지 알 수 없다.
        """
        queue = await self._require_queue(actor, queue_id, perms.QUEUE_WORK)
        return await issues.run_iql(
            self._s,
            self._perms,
            actor,
            queue.iql,
            request,
            extra_where=self._tickets_only(),
        )

    def _tickets_only(self) -> Any:
        """ "티켓인 이슈만" 조건.

        큐는 데스크의 화면이다. IQL 이 평범한 이슈까지 잡으면 상담원의 큐에
        고객과 무관한 이슈가 섞이고, 그 이슈에는 요청자도 창구도 없어서
        상담원 화면이 반쯤 빈 채로 그려진다.

        조건을 IQL 로 강제하지 않는 이유: `type = Request` 같은 규칙은
        관리자가 유형 이름을 바꾸는 순간 조용히 무력해진다. 티켓의 정의는
        `ticket_ext` 행이 있는 것이고, 그게 이름에 의존하지 않는 유일한
        근거다.
        """
        from sqlalchemy import exists, select

        return exists(
            select(TicketExt.issue_id).where(TicketExt.issue_id == issues.issue_id_column())
        )

    # ── 내부 ────────────────────────────────────────────────────

    async def _require_queue(self, actor: Actor, queue_id: UUID, permission: str) -> Queue:
        queue = await self._queues.get(queue_id)
        if queue is None or queue.archived_at is not None:
            raise NotFoundError("큐를 찾을 수 없다.")
        await self._perms.require(self._s, actor, permission, scope=Scope.project(queue.project_id))
        return queue

    async def _validated_name(
        self, project_id: UUID, raw: str, *, exclude: UUID | None = None
    ) -> str:
        name = raw.strip()
        if not name:
            raise ValidationError("이름을 비울 수 없다.", code="desk.queue_name_empty")
        if len(name) > NAME_MAX:
            raise ValidationError("이름이 너무 길다.", code="desk.queue_name_too_long")
        if await self._queues.name_taken(project_id, name, exclude=exclude):
            raise ConflictError("같은 이름의 큐가 있다.", code="desk.queue_name_taken")
        return name

    async def _validated_iql(self, actor: Actor, raw: str) -> str:
        """**저장할 때 질의를 검증한다.**

        요청 유형 폼과 같은 판단이다: 저장은 되고 실행이 실패하는 큐를 만들 수
        없게 한다. 그런 큐는 사이드바에 이름만 있고 누를 때만 실패하는데, 그
        사이 그 큐로 들어와야 할 티켓들은 **아무도 안 본다.** 폼과 달리 여기서는
        실패가 늦게 드러난다 — 큐를 만든 사람은 자기 큐를 눌러 보지 않는다.
        """
        iql = raw.strip()
        if not iql:
            raise ValidationError("조건을 비울 수 없다.", code="desk.queue_iql_empty")
        problem = await issues.validate_iql(self._s, self._perms, actor, iql)
        if problem is not None:
            raise ValidationError(problem.message, code=problem.code, details=problem.details)
        return iql


# ── 정형 응답 (C10) ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CannedResponseView:
    response: CannedResponse


class CannedResponseService:
    """정형 응답. 고치는 것은 `queue.manage`, 쓰는 것은 `queue.work` 다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._responses = CannedResponseRepository(session)

    async def create(
        self, actor: Actor, *, project_id: UUID, name: str, body: str, shortcut: str | None
    ) -> CannedResponseView:
        await self._perms.require(
            self._s, actor, perms.QUEUE_MANAGE, scope=Scope.project(project_id)
        )
        response = self._responses.add(
            CannedResponse(
                project_id=project_id,
                name=await self._validated_name(project_id, name),
                body=self._validated_body(body),
                shortcut=await self._validated_shortcut(project_id, shortcut),
            )
        )
        await self._s.flush()
        return CannedResponseView(response=response)

    async def update(
        self,
        actor: Actor,
        response_id: UUID,
        *,
        name: str | None = None,
        body: str | None = None,
        shortcut: str | None = None,
        clear_shortcut: bool = False,
    ) -> CannedResponseView:
        row = await self._require_response(actor, response_id, perms.QUEUE_MANAGE)
        if name is not None:
            row.name = await self._validated_name(row.project_id, name, exclude=row.id)
        if body is not None:
            row.body = self._validated_body(body)
        # 단축어를 **비우는 것**과 안 건드리는 것을 가른다. `None` 을 "지워라"
        # 로 읽으면 이름만 고치려는 요청이 단축어를 함께 날린다.
        if clear_shortcut:
            row.shortcut = None
        elif shortcut is not None:
            row.shortcut = await self._validated_shortcut(row.project_id, shortcut, exclude=row.id)
        await self._s.flush()
        return CannedResponseView(response=row)

    async def delete(self, actor: Actor, response_id: UUID) -> None:
        row = await self._require_response(actor, response_id, perms.QUEUE_MANAGE)
        row.archived_at = utcnow()
        await self._s.flush()

    async def list_for(self, actor: Actor, project_id: UUID) -> list[CannedResponse]:
        await self._perms.require(self._s, actor, perms.QUEUE_WORK, scope=Scope.project(project_id))
        return await self._responses.list_for_project(project_id)

    # ── 내부 ────────────────────────────────────────────────────

    async def _require_response(
        self, actor: Actor, response_id: UUID, permission: str
    ) -> CannedResponse:
        row = await self._responses.get(response_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("정형 응답을 찾을 수 없다.")
        await self._perms.require(self._s, actor, permission, scope=Scope.project(row.project_id))
        return row

    async def _validated_name(
        self, project_id: UUID, raw: str, *, exclude: UUID | None = None
    ) -> str:
        name = raw.strip()
        if not name:
            raise ValidationError("이름을 비울 수 없다.", code="desk.canned_name_empty")
        if len(name) > NAME_MAX:
            raise ValidationError("이름이 너무 길다.", code="desk.canned_name_too_long")
        if await self._responses.name_taken(project_id, name, exclude=exclude):
            raise ConflictError("같은 이름의 정형 응답이 있다.", code="desk.canned_name_taken")
        return name

    def _validated_body(self, raw: str) -> str:
        from ieum.core.markdown import normalize as normalize_markdown

        body = normalize_markdown(raw)
        if not body:
            raise ValidationError("내용을 비울 수 없다.", code="desk.canned_body_empty")
        return body

    async def _validated_shortcut(
        self, project_id: UUID, raw: str | None, *, exclude: UUID | None = None
    ) -> str | None:
        if raw is None:
            return None
        shortcut = raw.strip().lstrip("/")
        if not shortcut:
            return None
        if not SHORTCUT_PATTERN.match(shortcut):
            raise ValidationError(
                "단축어에는 공백을 쓸 수 없다.", code="desk.canned_shortcut_invalid"
            )
        if await self._responses.shortcut_taken(project_id, shortcut, exclude=exclude):
            raise ConflictError("같은 단축어가 있다.", code="desk.canned_shortcut_taken")
        return shortcut


# ── SLA 정책과 업무 달력 (C4) ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class CalendarView:
    calendar: BusinessCalendarRow


@dataclass(frozen=True, slots=True)
class SlaPolicyView:
    policy: SlaPolicy
    #: 달력 이름. 정책 목록에서 "무엇으로 재는지" 를 바로 보여 준다 —
    #: id 만 주면 화면이 달력 목록을 또 받아 짜맞춰야 하고, 목록에 없는
    #: 달력을 쓰는 정책은 영영 이름이 안 뜬다.
    calendar_name: str
    #: 에스컬레이션 규칙이 지목한 사람의 이름. id → 이름.
    #:
    #: **규칙 안에 넣지 않는다.** 저장 요청은 읽은 규칙을 그대로 되돌려
    #: 보내는데, 그 안에 이름이 섞여 있으면 `extra="forbid"` 가 거절한다 —
    #: 화면이 읽은 것을 그대로 저장할 수 없게 된다.
    escalation_user_names: dict[UUID, str] = field(default_factory=dict)


class SlaAdminService:
    """SLA 정책과 업무 달력 정의. **전역 + step-up 이다** (permissions.py).

    저장할 때 다 본다. 요청 유형 폼·큐 조건과 같은 판단인데, SLA 는 그보다
    늦게 드러난다: 망가진 정책은 저장되고, 클럭이 안 걸리고, 화면의 SLA 칸이
    비어 있을 뿐이다 — 아무도 그것이 빠졌다는 것을 모른다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    # ── 업무 달력 ───────────────────────────────────────────────

    async def list_calendars(self, actor: Actor) -> list[BusinessCalendarRow]:
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        rows = await self._s.execute(
            select(BusinessCalendarRow)
            .where(BusinessCalendarRow.archived_at.is_(None))
            .order_by(BusinessCalendarRow.name)
        )
        return list(rows.scalars().all())

    async def create_calendar(
        self,
        actor: Actor,
        *,
        name: str,
        timezone: str,
        working_hours: dict[str, Any],
        holidays: list[str],
    ) -> CalendarView:
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("이름을 비울 수 없다.", code="desk.calendar_name_empty")
        # **저장 전에 계산기에 넣어 본다.** 모양이 틀린 달력이 통과하면 SLA 가
        # 조용히 이상한 숫자를 내고, 그건 며칠 뒤에 "SLA 가 안 맞는다" 로만
        # 보인다. 업무 시간이 하루도 없는 달력도 여기서 걸린다.
        self._validated_calendar(timezone=timezone, working_hours=working_hours, holidays=holidays)
        if await self._calendar_name_taken(clean_name):
            raise ConflictError("같은 이름의 달력이 있다.", code="desk.calendar_name_taken")
        row = BusinessCalendarRow(
            name=clean_name,
            timezone=timezone,
            working_hours=working_hours,
            holidays=holidays,
        )
        self._s.add(row)
        await self._s.flush()
        return CalendarView(calendar=row)

    async def update_calendar(
        self,
        actor: Actor,
        calendar_id: UUID,
        *,
        name: str | None = None,
        timezone: str | None = None,
        working_hours: dict[str, Any] | None = None,
        holidays: list[str] | None = None,
    ) -> CalendarView:
        """달력을 고친다.

        **이미 걸린 클럭의 목표(`target_at`)는 움직이지 않는다.** 목표는
        저장되어 있고, 설정 변경으로 지난 티켓의 판정이 바뀌면 어제 지킨
        약속이 오늘 깨진 것이 된다. 새로 걸리는 클럭부터 새 달력을 쓴다.
        """
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        row = await self._s.get(BusinessCalendarRow, calendar_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("달력을 찾을 수 없다.")
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("이름을 비울 수 없다.", code="desk.calendar_name_empty")
            if await self._calendar_name_taken(clean, exclude=row.id):
                raise ConflictError("같은 이름의 달력이 있다.", code="desk.calendar_name_taken")
            row.name = clean
        self._validated_calendar(
            timezone=timezone if timezone is not None else row.timezone,
            working_hours=working_hours if working_hours is not None else dict(row.working_hours),
            holidays=holidays if holidays is not None else list(row.holidays),
        )
        if timezone is not None:
            row.timezone = timezone
        if working_hours is not None:
            row.working_hours = working_hours
        if holidays is not None:
            row.holidays = holidays
        await self._s.flush()
        return CalendarView(calendar=row)

    async def delete_calendar(self, actor: Actor, calendar_id: UUID) -> None:
        """보관한다. **쓰는 정책이 있으면 거절한다.**

        FK 가 `RESTRICT` 라 지우면 DB 오류로 500 이 되고, 관리자는 무엇이
        막았는지 못 듣는다. 보관도 같이 막는다 — 보관된 달력을 쓰는 정책은
        화면에서 달력 이름을 잃는다.
        """
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        row = await self._s.get(BusinessCalendarRow, calendar_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("달력을 찾을 수 없다.")
        using = (
            await self._s.execute(
                select(func.count()).where(
                    SlaPolicy.calendar_id == calendar_id, SlaPolicy.archived_at.is_(None)
                )
            )
        ).scalar_one()
        if using:
            raise ConflictError("이 달력을 쓰는 SLA 정책이 있다.", code="desk.calendar_in_use")
        row.archived_at = utcnow()
        await self._s.flush()

    # ── SLA 정책 ────────────────────────────────────────────────

    async def list_states(self, actor: Actor, project_id: UUID) -> list[issues.StateRef]:
        """이 프로젝트에서 고를 수 있는 상태. 멈춤 상태 선택 목록이 쓴다.

        **UUID 를 손으로 적게 하지 않는다.** 서버가 모르는 상태를 거절하게
        만들었으니, 화면에는 고를 수 있는 것만 보여야 한다 — 거절만 하고
        무엇을 고를 수 있는지 안 알려 주면 관리자는 막힌다.
        """
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        return await issues.get_project_states(self._s, project_id)

    async def list_policies(self, actor: Actor, project_id: UUID) -> list[SlaPolicyView]:
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        rows = await self._s.execute(
            select(SlaPolicy, BusinessCalendarRow.name)
            .join(BusinessCalendarRow, BusinessCalendarRow.id == SlaPolicy.calendar_id)
            .where(SlaPolicy.project_id == project_id, SlaPolicy.archived_at.is_(None))
            .order_by(SlaPolicy.name)
        )
        found = list(rows.all())
        # **이름을 한 번에 모은다.** 행마다 조회하면 정책 열 개짜리 프로젝트가
        # 열 번 왕복한다 — 큐 목록에서 이미 겪은 N+1 이다.
        names = await self._escalation_names([policy for policy, _ in found])
        return [
            SlaPolicyView(policy=policy, calendar_name=name, escalation_user_names=names)
            for policy, name in found
        ]

    async def create_policy(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        name: str,
        metric: str,
        calendar_id: UUID,
        goals: list[dict[str, Any]],
        pause_state_ids: list[UUID],
        escalations: list[dict[str, Any]] | None = None,
    ) -> SlaPolicyView:
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("이름을 비울 수 없다.", code="desk.sla_name_empty")
        if metric not in SLA_METRICS:
            raise ValidationError("모르는 지표다.", code="desk.sla_unknown_metric")
        calendar = await self._s.get(BusinessCalendarRow, calendar_id)
        if calendar is None or calendar.archived_at is not None:
            raise ValidationError("그 달력이 없다.", code="desk.sla_calendar_missing")
        try:
            checked = validate_goals(goals)
        except SlaError as exc:
            raise ValidationError(str(exc), code="desk.sla_goals_invalid") from exc
        try:
            checked_rules = validate_escalations(escalations)
        except SlaError as exc:
            raise ValidationError(str(exc), code="desk.sla_escalation_invalid") from exc
        if await self._policy_name_taken(project_id, clean_name):
            raise ConflictError("같은 이름의 정책이 있다.", code="desk.sla_name_taken")
        await self._validated_pause_states(project_id, pause_state_ids)
        row = SlaPolicy(
            project_id=project_id,
            name=clean_name,
            metric=metric,
            calendar_id=calendar_id,
            goals=checked,
            pause_state_ids=list(pause_state_ids),
            escalations=checked_rules,
        )
        self._s.add(row)
        await self._s.flush()
        return SlaPolicyView(
            policy=row,
            calendar_name=calendar.name,
            escalation_user_names=await self._escalation_names([row]),
        )

    async def update_policy(
        self,
        actor: Actor,
        policy_id: UUID,
        *,
        name: str | None = None,
        calendar_id: UUID | None = None,
        goals: list[dict[str, Any]] | None = None,
        pause_state_ids: list[UUID] | None = None,
        escalations: list[dict[str, Any]] | None = None,
        is_enabled: bool | None = None,
    ) -> SlaPolicyView:
        """정책을 고친다.

        **`metric` 은 바꿀 수 없다.** 응답 정책을 해결 정책으로 바꾸면 이미
        걸린 클럭들이 갑자기 다른 것을 재는 시계가 된다 — 지난 지표가 통째로
        뜻을 잃는다. 새 정책을 만들고 이것을 끄는 것이 옳은 길이다.
        """
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        row = await self._s.get(SlaPolicy, policy_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("정책을 찾을 수 없다.")
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("이름을 비울 수 없다.", code="desk.sla_name_empty")
            if await self._policy_name_taken(row.project_id, clean, exclude=row.id):
                raise ConflictError("같은 이름의 정책이 있다.", code="desk.sla_name_taken")
            row.name = clean
        if calendar_id is not None:
            calendar = await self._s.get(BusinessCalendarRow, calendar_id)
            if calendar is None or calendar.archived_at is not None:
                raise ValidationError("그 달력이 없다.", code="desk.sla_calendar_missing")
            row.calendar_id = calendar_id
        if goals is not None:
            try:
                row.goals = validate_goals(goals)
            except SlaError as exc:
                raise ValidationError(str(exc), code="desk.sla_goals_invalid") from exc
        if pause_state_ids is not None:
            await self._validated_pause_states(row.project_id, pause_state_ids)
            row.pause_state_ids = list(pause_state_ids)
        if escalations is not None:
            try:
                row.escalations = validate_escalations(escalations)
            except SlaError as exc:
                raise ValidationError(str(exc), code="desk.sla_escalation_invalid") from exc
        if is_enabled is not None:
            row.is_enabled = is_enabled
        await self._s.flush()
        name_of = await self._s.get(BusinessCalendarRow, row.calendar_id)
        return SlaPolicyView(
            policy=row,
            calendar_name=name_of.name if name_of else "",
            escalation_user_names=await self._escalation_names([row]),
        )

    async def delete_policy(self, actor: Actor, policy_id: UUID) -> None:
        """보관한다. **이미 걸린 클럭은 그대로 둔다** — 지난 티켓의 판정이
        정책을 지우는 것으로 바뀌면 안 된다."""
        await self._perms.require(self._s, actor, perms.SLA_MANAGE)
        row = await self._s.get(SlaPolicy, policy_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("정책을 찾을 수 없다.")
        row.archived_at = utcnow()
        await self._s.flush()

    # ── 내부 ────────────────────────────────────────────────────

    async def _escalation_names(self, policies: list[SlaPolicy]) -> dict[UUID, str]:
        """규칙이 지목한 사람들의 이름. 없으면 빈 사전.

        지워진 계정은 **빠진다.** 그러면 화면이 id 를 그대로 보여 주는데,
        조용히 규칙을 감추는 것보다 낫다 — "누구를 부르기로 했는지 모르는
        규칙" 이 남아 있다는 사실 자체가 보여야 고칠 수 있다.
        """
        wanted = {
            UUID(str(rule["user_id"]))
            for policy in policies
            for rule in policy.escalations
            if rule.get("user_id")
        }
        if not wanted:
            return {}
        found = await identity.get_users(self._s, wanted)
        return {user_id: ref.display_name for user_id, ref in found.items()}

    async def _validated_pause_states(self, project_id: UUID, state_ids: Sequence[UUID]) -> None:
        """멈춤 상태가 **이 프로젝트에 실제로 있는 상태**인지 본다.

        검증 없이 받으면 잘못된 UUID 가 그대로 저장되고, 시계는 영원히 안
        멈춘다 — 관리자는 멈춤을 설정했다고 믿고, 아무 일도 일어나지 않으며,
        틀렸다는 신호가 어디에도 없다. 저장할 때 거절하는 것이 유일하게
        사람이 알 수 있는 지점이다.
        """
        if not state_ids:
            return
        known = {row.id for row in await issues.get_project_states(self._s, project_id)}
        unknown = [sid for sid in state_ids if sid not in known]
        if unknown:
            raise ValidationError("이 프로젝트에 없는 상태다.", code="desk.sla_pause_state_unknown")

    def _validated_calendar(
        self, *, timezone: str, working_hours: dict[str, Any], holidays: list[str]
    ) -> None:
        try:
            parse_calendar(timezone=timezone, working_hours=working_hours, holidays=holidays)
        except CalendarError as exc:
            raise ValidationError(str(exc), code="desk.calendar_invalid") from exc

    async def _calendar_name_taken(self, name: str, *, exclude: UUID | None = None) -> bool:
        stmt = select(func.count()).where(BusinessCalendarRow.name == name)
        if exclude is not None:
            stmt = stmt.where(BusinessCalendarRow.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())

    async def _policy_name_taken(
        self, project_id: UUID, name: str, *, exclude: UUID | None = None
    ) -> bool:
        stmt = select(func.count()).where(
            SlaPolicy.project_id == project_id, SlaPolicy.name == name
        )
        if exclude is not None:
            stmt = stmt.where(SlaPolicy.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())


@dataclass(frozen=True, slots=True)
class EmailChannelView:
    """화면에 내려가는 채널. **비밀번호가 없다.**

    `has_password` 만 준다: 관리자는 "설정돼 있는가" 를 알아야 하고, 값 자체를
    되돌려 받을 이유는 없다. 되돌려주면 그 값이 브라우저의 메모리·로그·오류
    보고를 거쳐 다니게 된다.
    """

    channel: EmailChannel
    #: 이 채널이 만드는 요청 유형의 이름. id 만 주면 화면이 목록을 또 받아
    #: 짜맞춰야 한다 — SLA 정책의 달력 이름과 같은 판단이다.
    request_type_name: str
    has_password: bool


class EmailChannelService:
    """메일 채널 정의 (feature-map C6). **프로젝트 + step-up 이다.**

    비밀번호를 받는 자리이고, 받는 주소를 바꾸면 그 뒤로 오는 고객의 메일이
    다른 프로젝트의 티켓이 된다 (permissions.py 의 `EMAIL_MANAGE`).
    """

    def __init__(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        self._s = session
        self._perms = permissions
        self._box = SecretBox(settings.secret_key.get_secret_value(), purpose="desk.email")

    async def list_for(self, actor: Actor, project_id: UUID) -> list[EmailChannelView]:
        await self._perms.require(
            self._s, actor, perms.EMAIL_MANAGE, scope=Scope.project(project_id)
        )
        rows = (
            await self._s.execute(
                select(EmailChannel, RequestType.name)
                .join(RequestType, RequestType.id == EmailChannel.default_request_type_id)
                .where(
                    EmailChannel.project_id == project_id,
                    EmailChannel.archived_at.is_(None),
                )
                .order_by(EmailChannel.address)
            )
        ).all()
        return [
            EmailChannelView(
                channel=channel,
                request_type_name=name,
                has_password=bool(channel.inbound_password_enc),
            )
            for channel, name in rows
        ]

    async def create(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        address: str,
        outbound_from: str,
        inbound: dict[str, Any],
        password: str | None,
        default_request_type_id: UUID,
    ) -> EmailChannelView:
        await self._perms.require(
            self._s, actor, perms.EMAIL_MANAGE, scope=Scope.project(project_id)
        )
        clean_address = self._validated_address(address)
        clean_from = self._validated_address(outbound_from)
        request_type = await self._request_type_in(project_id, default_request_type_id)
        config = self._validated_inbound(inbound, password=password)

        if await self._address_taken(clean_address):
            raise ConflictError("그 주소를 쓰는 채널이 있다.", code="desk.email_address_taken")

        row = EmailChannel(
            project_id=project_id,
            address=clean_address,
            outbound_from=clean_from,
            inbound=config,
            inbound_password_enc=self._box.encrypt(password) if password else None,
            default_request_type_id=request_type.id,
        )
        self._s.add(row)
        await self._s.flush()
        log.info("desk.email_channel.created", channel_id=str(row.id), address=clean_address)
        return EmailChannelView(
            channel=row,
            request_type_name=request_type.name,
            has_password=bool(row.inbound_password_enc),
        )

    async def update(
        self,
        actor: Actor,
        channel_id: UUID,
        *,
        address: str | None = None,
        outbound_from: str | None = None,
        inbound: dict[str, Any] | None = None,
        password: str | None = None,
        default_request_type_id: UUID | None = None,
        is_enabled: bool | None = None,
    ) -> EmailChannelView:
        """고친다. **비밀번호를 안 보내면 그대로 둔다.**

        빈 문자열을 "지우기" 로 읽지 않는다: 폼이 비밀번호 칸을 비워 두고
        보내는 것이 정상 동작이라(값을 되돌려주지 않으므로) 그것을 지우기로
        읽으면 이름만 고쳐도 메일 수신이 멈춘다.
        """
        row = await self._s.get(EmailChannel, channel_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("메일 채널을 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.EMAIL_MANAGE, scope=Scope.project(row.project_id)
        )

        if address is not None:
            clean = self._validated_address(address)
            if clean != row.address and await self._address_taken(clean):
                raise ConflictError("그 주소를 쓰는 채널이 있다.", code="desk.email_address_taken")
            row.address = clean
        if outbound_from is not None:
            row.outbound_from = self._validated_address(outbound_from)
        if default_request_type_id is not None:
            row.default_request_type_id = (
                await self._request_type_in(row.project_id, default_request_type_id)
            ).id
        if inbound is not None:
            # 비밀번호가 이미 저장돼 있으면 설정만 바꿔도 성립한다.
            row.inbound = self._validated_inbound(
                inbound, password=password or ("kept" if row.inbound_password_enc else None)
            )
        if password:
            row.inbound_password_enc = self._box.encrypt(password)
        if is_enabled is not None:
            row.is_enabled = is_enabled
            if is_enabled:
                # 다시 켤 때 지난 오류를 지운다. 남겨 두면 관리자는 아직
                # 망가진 줄 알고 그 메시지를 다시는 믿지 않게 된다.
                row.last_error = None
        await self._s.flush()
        request_type = await self._s.get(RequestType, row.default_request_type_id)
        return EmailChannelView(
            channel=row,
            request_type_name=request_type.name if request_type else "",
            has_password=bool(row.inbound_password_enc),
        )

    async def delete(self, actor: Actor, channel_id: UUID) -> None:
        """보관한다. **오간 메일의 기록은 그대로 둔다** — 지난 티켓의 스레드가
        채널을 지우는 것으로 끊기면 안 된다."""
        row = await self._s.get(EmailChannel, channel_id)
        if row is None or row.archived_at is not None:
            raise NotFoundError("메일 채널을 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.EMAIL_MANAGE, scope=Scope.project(row.project_id)
        )
        row.archived_at = utcnow()
        await self._s.flush()

    # ── 내부 ────────────────────────────────────────────────────

    @staticmethod
    def _validated_address(raw: str) -> str:
        clean = raw.strip().lower()
        if not EMAIL_PATTERN.match(clean):
            raise ValidationError("이메일 모양이 아니다.", code="desk.invalid_email")
        return clean

    async def _address_taken(self, address: str) -> bool:
        """보관한 채널까지 본다. `address` 가 DB 에서 유일하기 때문이다 —
        보관한 것을 안 보면 저장이 IntegrityError 로 500 이 되고, 관리자는
        무엇이 막았는지 못 듣는다."""
        found = await self._s.execute(
            select(EmailChannel.id).where(EmailChannel.address == address).limit(1)
        )
        return found.first() is not None

    async def _request_type_in(self, project_id: UUID, request_type_id: UUID) -> RequestType:
        """이 프로젝트의 요청 유형인가.

        안 보면 다른 프로젝트의 폼으로 티켓을 만들게 되고, 그 티켓은 이
        프로젝트의 큐에 안 걸린다 — 메일은 들어왔는데 아무도 못 본다.
        """
        row = await self._s.get(RequestType, request_type_id)
        if row is None or row.archived_at is not None:
            raise ValidationError("그 요청 유형이 없다.", code="desk.unknown_request_type")
        portal = await self._portals_get(row.portal_id)
        if portal is None or portal.project_id != project_id:
            raise ValidationError(
                "이 프로젝트의 요청 유형이 아니다.", code="desk.unknown_request_type"
            )
        return row

    async def _portals_get(self, portal_id: UUID) -> Portal | None:
        return await self._s.get(Portal, portal_id)

    @staticmethod
    def _validated_inbound(config: dict[str, Any], *, password: str | None) -> dict[str, Any]:
        """설정을 저장 전에 IMAP 파서에 넣어 본다.

        **저장되고 폴링만 실패하는 것을 만들지 않는다.** 그 실패는 채널의
        `last_error` 로만 보이고, 관리자는 저장이 성공했으니 됐다고 믿는다 —
        SLA 정책·큐 조건과 같은 판단이다.

        비밀번호는 여기서 모양만 쓰인다(파서가 요구한다). 값은 따로 암호화해
        저장한다.
        """
        from ieum.modules.desk.imap import ImapError, ImapSettings

        if not password:
            raise ValidationError(
                "메일함 비밀번호가 필요하다.", code="desk.email_password_required"
            )
        allowed = {"host", "port", "user", "folder", "use_ssl"}
        unknown = set(config) - allowed
        if unknown:
            raise ValidationError(
                f"모르는 설정: {sorted(unknown)}", code="desk.email_inbound_invalid"
            )
        try:
            ImapSettings.parse(config, password)
        except ImapError as exc:
            raise ValidationError(str(exc), code="desk.email_inbound_invalid") from exc
        return {key: config[key] for key in sorted(config)}
