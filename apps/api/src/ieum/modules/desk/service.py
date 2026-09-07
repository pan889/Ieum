"""desk 비즈니스 로직. 권한 검사와 도메인 규칙이 여기 있다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
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
from ieum.modules.desk.events import TicketSubmitted
from ieum.modules.desk.models import (
    RESERVED_FORM_KEYS,
    CustomerOrganization,
    Portal,
    RequestType,
    TicketExt,
)
from ieum.modules.desk.repository import (
    CustomerMembershipRepository,
    CustomerOrganizationRepository,
    PortalRepository,
    RequestTypeRepository,
    TicketRepository,
    normalize_domain,
    normalize_slug,
)
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
        )

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
            _guest_actor(clean_email),
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


def _guest_actor(email: str) -> Actor:
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
