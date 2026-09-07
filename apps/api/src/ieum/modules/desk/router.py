"""desk 라우터. 내부 관리 표면과 고객 포털 표면을 **분리해** 둔다.

**쓰기 라우트는 끝에서 커밋한다.** `conventions.md` 는 트랜잭션 경계가
서비스라고 정해 두었지만 이 저장소의 실제 관행은 라우터 커밋이다 — 여섯
모듈이 모두 그렇게 되어 있어서 desk 만 다르게 두면 그 불일치가 더 나쁘다.
빠뜨리면 API 가 **201 을 주고 아무것도 저장하지 않는다**(실제로 그랬다 —
서비스 시험은 픽스처가 트랜잭션을 들고 있어서 전부 통과했고, HTTP 로 직접
몰아 보고서야 드러났다). 그래서 `test_desk_api.py` 가 쓴 다음 **다른
요청**으로 읽어 그 종류를 붙잡는다.

포털 라우터의 prefix 가 `/portal` 인 것은 우연이 아니다: `core/deps.py` 의
고객 격리가 그 접두사로 통과 여부를 가른다 (auth.md 5절). 여기 라우트를
`/portals` 쪽으로 옮기면 고객은 그 자리에서 403 을 받는다.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.desk.schemas import (
    CustomerMemberResponse,
    CustomerOrgCreateRequest,
    CustomerOrgResponse,
    CustomerOrgUpdateRequest,
    GuestSubmitRequest,
    MembershipRequest,
    PortalCreateRequest,
    PortalFormFieldResponse,
    PortalFormResponse,
    PortalInfoResponse,
    PortalRequestTypeResponse,
    PortalResponse,
    PortalSubmitRequest,
    PortalUpdateRequest,
    RequestTypeCreateRequest,
    RequestTypeResponse,
    RequestTypeUpdateRequest,
    TicketResponse,
    TicketSummaryResponse,
)
from ieum.modules.desk.service import (
    CustomerOrgService,
    CustomerOrgView,
    CustomerPortalService,
    PortalForm,
    PortalService,
    PortalView,
    RequestTypeView,
    TicketView,
)

#: 내부 관리 표면. 상담원·관리자가 쓴다.
portals_router = APIRouter(prefix="/portals", tags=["desk"])
customers_router = APIRouter(prefix="/customer-organizations", tags=["desk"])
#: 고객이 쓰는 표면. 이 접두사만 고객 격리를 통과한다.
portal_router = APIRouter(prefix="/portal", tags=["portal"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _portal(view: PortalView) -> PortalResponse:
    return PortalResponse(
        id=view.portal.id,
        project_id=view.portal.project_id,
        name=view.portal.name,
        slug=view.portal.slug,
        description=view.portal.description,
        theme=view.portal.theme,
        is_public=view.portal.is_public,
        is_archived=view.portal.is_archived,
        request_type_count=view.request_type_count,
    )


def _request_type(view: RequestTypeView) -> RequestTypeResponse:
    row = view.request_type
    return RequestTypeResponse(
        id=row.id,
        portal_id=row.portal_id,
        issue_type_id=row.issue_type_id,
        issue_type_name=view.issue_type_name,
        name=row.name,
        description=row.description,
        icon=row.icon,
        position=row.position,
        form_schema=row.form_schema,
        field_mapping=row.field_mapping,
        is_enabled=row.is_enabled,
        is_archived=row.is_archived,
        ticket_count=view.ticket_count,
    )


def _organization(view: CustomerOrgView) -> CustomerOrgResponse:
    row = view.organization
    return CustomerOrgResponse(
        id=row.id,
        name=row.name,
        domains=list(row.domains),
        note=row.note,
        is_archived=row.is_archived,
        member_count=view.member_count,
    )


def _ticket(view: TicketView) -> TicketResponse:
    return TicketResponse(
        id=view.issue.id,
        key=view.issue.key,
        summary=view.issue.summary,
        description=view.issue.description,
        state_name=view.issue.state_name,
        state_category=view.issue.state_category,
        created_at=view.issue.created_at,
        updated_at=view.issue.updated_at,
        request_type_name=view.request_type_name,
        answers=view.answers,
    )


def _ticket_summary(view: TicketView) -> TicketSummaryResponse:
    return TicketSummaryResponse(
        id=view.issue.id,
        key=view.issue.key,
        summary=view.issue.summary,
        state_name=view.issue.state_name,
        state_category=view.issue.state_category,
        created_at=view.issue.created_at,
        updated_at=view.issue.updated_at,
        request_type_name=view.request_type_name,
    )


def _form(form: PortalForm) -> PortalFormResponse:
    return PortalFormResponse(
        request_type=PortalRequestTypeResponse(
            id=form.request_type.id,
            name=form.request_type.name,
            description=form.request_type.description,
            icon=form.request_type.icon,
        ),
        fields=[
            PortalFormFieldResponse(
                key=f.key,
                label=f.label,
                help=f.help,
                required=f.required,
                kind=f.kind,
                config=f.config,
            )
            for f in form.fields
        ],
    )


# ── 포털 정의 (내부) ────────────────────────────────────────────


@portals_router.get("", response_model=list[PortalResponse])
async def list_portals(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    project_id: UUID,
    include_archived: bool = False,
) -> list[PortalResponse]:
    views = await PortalService(session, permissions).list_for_project(
        actor, project_id, include_archived=include_archived
    )
    return [_portal(v) for v in views]


@portals_router.post("", response_model=PortalResponse, status_code=status.HTTP_201_CREATED)
async def create_portal(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: PortalCreateRequest,
) -> PortalResponse:
    view = await PortalService(session, permissions).create(
        actor,
        project_id=payload.project_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        theme=payload.theme,
        is_public=payload.is_public,
    )
    await session.commit()
    return _portal(view)


@portals_router.get("/{portal_id}", response_model=PortalResponse)
async def get_portal(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, portal_id: UUID
) -> PortalResponse:
    return _portal(await PortalService(session, permissions).get(actor, portal_id))


@portals_router.patch("/{portal_id}", response_model=PortalResponse)
async def update_portal(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    portal_id: UUID,
    payload: PortalUpdateRequest,
) -> PortalResponse:
    view = await PortalService(session, permissions).update(
        actor,
        portal_id,
        name=payload.name,
        description=payload.description,
        theme=payload.theme,
        is_public=payload.is_public,
    )
    await session.commit()
    return _portal(view)


@portals_router.post("/{portal_id}/archive", response_model=PortalResponse)
async def archive_portal(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, portal_id: UUID
) -> PortalResponse:
    view = await PortalService(session, permissions).set_archived(actor, portal_id, archived=True)
    await session.commit()
    return _portal(view)


@portals_router.delete("/{portal_id}/archive", response_model=PortalResponse)
async def restore_portal(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, portal_id: UUID
) -> PortalResponse:
    view = await PortalService(session, permissions).set_archived(actor, portal_id, archived=False)
    await session.commit()
    return _portal(view)


# ── 요청 유형 (내부) ────────────────────────────────────────────


@portals_router.get("/{portal_id}/request-types", response_model=list[RequestTypeResponse])
async def list_request_types(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, portal_id: UUID
) -> list[RequestTypeResponse]:
    _, views = await PortalService(session, permissions).list_request_types(actor, portal_id)
    return [_request_type(v) for v in views]


@portals_router.post(
    "/{portal_id}/request-types",
    response_model=RequestTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_request_type(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    portal_id: UUID,
    payload: RequestTypeCreateRequest,
) -> RequestTypeResponse:
    view = await PortalService(session, permissions).create_request_type(
        actor,
        portal_id,
        issue_type_id=payload.issue_type_id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        position=payload.position,
        form_fields=[f.model_dump() for f in payload.form_schema.fields],
        field_mapping=payload.field_mapping,
        is_enabled=payload.is_enabled,
    )
    await session.commit()
    return _request_type(view)


@portals_router.patch("/request-types/{request_type_id}", response_model=RequestTypeResponse)
async def update_request_type(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    request_type_id: UUID,
    payload: RequestTypeUpdateRequest,
) -> RequestTypeResponse:
    view = await PortalService(session, permissions).update_request_type(
        actor,
        request_type_id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        position=payload.position,
        form_fields=(
            [f.model_dump() for f in payload.form_schema.fields]
            if payload.form_schema is not None
            else None
        ),
        field_mapping=payload.field_mapping,
        is_enabled=payload.is_enabled,
    )
    await session.commit()
    return _request_type(view)


@portals_router.post("/request-types/{request_type_id}/archive", response_model=RequestTypeResponse)
async def archive_request_type(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, request_type_id: UUID
) -> RequestTypeResponse:
    view = await PortalService(session, permissions).archive_request_type(
        actor, request_type_id, archived=True
    )
    await session.commit()
    return _request_type(view)


@portals_router.delete(
    "/request-types/{request_type_id}/archive", response_model=RequestTypeResponse
)
async def restore_request_type(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, request_type_id: UUID
) -> RequestTypeResponse:
    view = await PortalService(session, permissions).archive_request_type(
        actor, request_type_id, archived=False
    )
    await session.commit()
    return _request_type(view)


# ── 고객 조직 (내부) ────────────────────────────────────────────


class CustomerOrgPage(dict[str, Any]):
    """FastAPI 응답 모델을 쓰지 않는다 — 목록은 커서 봉투로 감싼다."""


@customers_router.get("")
async def list_customer_orgs(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    page = await CustomerOrgService(session, permissions).list_all(
        actor, PageRequest(limit=limit, cursor=cursor), query=q
    )
    return {
        "items": [_organization(v).model_dump(mode="json") for v in page.items],
        "next_cursor": page.next_cursor,
    }


@customers_router.post("", response_model=CustomerOrgResponse, status_code=status.HTTP_201_CREATED)
async def create_customer_org(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: CustomerOrgCreateRequest,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions).create(
        actor, name=payload.name, domains=payload.domains, note=payload.note
    )
    await session.commit()
    return _organization(view)


@customers_router.patch("/{organization_id}", response_model=CustomerOrgResponse)
async def update_customer_org(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    organization_id: UUID,
    payload: CustomerOrgUpdateRequest,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions).update(
        actor, organization_id, name=payload.name, domains=payload.domains, note=payload.note
    )
    await session.commit()
    return _organization(view)


@customers_router.post("/{organization_id}/archive", response_model=CustomerOrgResponse)
async def archive_customer_org(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, organization_id: UUID
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions).set_archived(
        actor, organization_id, archived=True
    )
    await session.commit()
    return _organization(view)


@customers_router.delete("/{organization_id}/archive", response_model=CustomerOrgResponse)
async def restore_customer_org(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, organization_id: UUID
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions).set_archived(
        actor, organization_id, archived=False
    )
    await session.commit()
    return _organization(view)


@customers_router.get("/{organization_id}/members", response_model=list[CustomerMemberResponse])
async def list_customer_members(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, organization_id: UUID
) -> list[CustomerMemberResponse]:
    users = await CustomerOrgService(session, permissions).list_members(actor, organization_id)
    return [
        CustomerMemberResponse(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            status="active" if u.is_active else "inactive",
        )
        for u in users
    ]


@customers_router.post(
    "/{organization_id}/members",
    response_model=CustomerMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_customer_member(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    organization_id: UUID,
    payload: MembershipRequest,
) -> CustomerMemberResponse:
    user = await CustomerOrgService(session, permissions).add_member(
        actor, organization_id, user_id=payload.user_id
    )
    await session.commit()
    return CustomerMemberResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        status="active" if user.is_active else "inactive",
    )


@customers_router.delete(
    "/{organization_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_customer_member(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    await CustomerOrgService(session, permissions).remove_member(
        actor, organization_id, user_id=user_id
    )
    await session.commit()


# ── 포털 (고객이 쓰는 쪽) ──────────────────────────────────────
#
# `/{slug}/...` 는 **로그인 없이** 열린다. 액터 의존성을 걸지 않는 것이
# 곧 그 뜻이다 — 걸면 게스트 요청이 401 로 막힌다.


@portal_router.get("/{slug}", response_model=PortalInfoResponse)
async def portal_info(
    session: DbSession, permissions: PermissionDep, slug: str
) -> PortalInfoResponse:
    portal = await CustomerPortalService(session, permissions).info(slug)
    return PortalInfoResponse(
        slug=portal.slug,
        name=portal.name,
        description=portal.description,
        theme=portal.theme,
        allows_guests=portal.is_public,
    )


@portal_router.get("/{slug}/request-types", response_model=list[PortalRequestTypeResponse])
async def portal_request_types(
    session: DbSession, permissions: PermissionDep, slug: str
) -> list[PortalRequestTypeResponse]:
    _, rows = await CustomerPortalService(session, permissions).list_forms(slug)
    return [
        PortalRequestTypeResponse(
            id=row.id, name=row.name, description=row.description, icon=row.icon
        )
        for row in rows
    ]


@portal_router.get("/{slug}/request-types/{request_type_id}", response_model=PortalFormResponse)
async def portal_form(
    session: DbSession, permissions: PermissionDep, slug: str, request_type_id: UUID
) -> PortalFormResponse:
    return _form(await CustomerPortalService(session, permissions).get_form(slug, request_type_id))


@portal_router.post(
    "/{slug}/requests", response_model=TicketResponse, status_code=status.HTTP_201_CREATED
)
async def portal_submit(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    slug: str,
    payload: PortalSubmitRequest,
) -> TicketResponse:
    view = await CustomerPortalService(session, permissions).submit(
        actor, slug, request_type_id=payload.request_type_id, answers=payload.answers
    )
    await session.commit()
    return _ticket(view)


@portal_router.post(
    "/{slug}/guest-requests", response_model=TicketResponse, status_code=status.HTTP_201_CREATED
)
async def portal_guest_submit(
    session: DbSession, permissions: PermissionDep, slug: str, payload: GuestSubmitRequest
) -> TicketResponse:
    """로그인 없는 제출. 액터 의존성이 **없다** — 그게 이 라우트의 요점이다."""
    view = await CustomerPortalService(session, permissions).submit_as_guest(
        slug,
        request_type_id=payload.request_type_id,
        email=payload.email,
        name=payload.name,
        answers=payload.answers,
    )
    await session.commit()
    return _ticket(view)


@portal_router.get("/{slug}/requests")
async def portal_my_requests(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    slug: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    page = await CustomerPortalService(session, permissions).list_my_tickets(
        actor, slug, PageRequest(limit=limit, cursor=cursor)
    )
    return {
        "items": [_ticket_summary(v).model_dump(mode="json") for v in page.items],
        "next_cursor": page.next_cursor,
    }


@portal_router.get("/{slug}/requests/{issue_id}", response_model=TicketResponse)
async def portal_my_request(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    slug: str,
    issue_id: UUID,
) -> TicketResponse:
    view = await CustomerPortalService(session, permissions).get_my_ticket(actor, slug, issue_id)
    return _ticket(view)
