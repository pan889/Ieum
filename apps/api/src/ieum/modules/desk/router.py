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

from ieum.core.deps import AppSettings, CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.modules.desk.schemas import (
    AgentTicketEnvelope,
    AgentTicketResponse,
    AnswerResponse,
    CalendarCreateRequest,
    CalendarResponse,
    CalendarUpdateRequest,
    CannedResponseCreateRequest,
    CannedResponseResponse,
    CannedResponseUpdateRequest,
    CustomerInviteRequest,
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
    QueueCreateRequest,
    QueueResponse,
    QueueTicketPageResponse,
    QueueTicketResponse,
    QueueUpdateRequest,
    ReplyRequest,
    ReplyResponse,
    RequesterResponse,
    RequestTypeCreateRequest,
    RequestTypeResponse,
    RequestTypeUpdateRequest,
    SlaPolicyCreateRequest,
    SlaPolicyResponse,
    SlaPolicyUpdateRequest,
    SlaStandingResponse,
    TicketResponse,
    TicketSummaryResponse,
)
from ieum.modules.desk.service import (
    AgentTicketService,
    CannedResponseService,
    CannedResponseView,
    CustomerOrgService,
    CustomerOrgView,
    CustomerPortalService,
    PortalForm,
    PortalService,
    PortalView,
    QueueService,
    QueueView,
    RequestTypeView,
    SlaAdminService,
    SlaPolicyView,
    TicketView,
)

#: 내부 관리 표면. 상담원·관리자가 쓴다.
portals_router = APIRouter(prefix="/portals", tags=["desk"])
customers_router = APIRouter(prefix="/customer-organizations", tags=["desk"])
#: 상담원이 티켓의 데스크 정보를 읽는 자리. 내부 표면이다.
tickets_router = APIRouter(prefix="/tickets", tags=["desk"])
#: 큐와 정형 응답. 상담원의 작업 표면이다.
queues_router = APIRouter(prefix="/queues", tags=["desk"])
canned_router = APIRouter(prefix="/canned-responses", tags=["desk"])
#: SLA. 달력은 설치 전체에서 공유하고, 정책은 프로젝트 단위다 — 둘 다
#: `desk.sla.manage`(전역 + step-up)가 필요하다.
calendars_router = APIRouter(prefix="/business-calendars", tags=["desk"])
sla_router = APIRouter(prefix="/sla-policies", tags=["desk"])
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
        answers=[AnswerResponse(key=a.key, label=a.label, value=a.value) for a in view.answers],
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
    settings: AppSettings,
    actor: CurrentActor,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    page = await CustomerOrgService(session, permissions, settings).list_all(
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
    settings: AppSettings,
    actor: CurrentActor,
    payload: CustomerOrgCreateRequest,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions, settings).create(
        actor, name=payload.name, domains=payload.domains, note=payload.note
    )
    await session.commit()
    return _organization(view)


@customers_router.patch("/{organization_id}", response_model=CustomerOrgResponse)
async def update_customer_org(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
    payload: CustomerOrgUpdateRequest,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions, settings).update(
        actor, organization_id, name=payload.name, domains=payload.domains, note=payload.note
    )
    await session.commit()
    return _organization(view)


@customers_router.post("/{organization_id}/archive", response_model=CustomerOrgResponse)
async def archive_customer_org(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions, settings).set_archived(
        actor, organization_id, archived=True
    )
    await session.commit()
    return _organization(view)


@customers_router.delete("/{organization_id}/archive", response_model=CustomerOrgResponse)
async def restore_customer_org(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
) -> CustomerOrgResponse:
    view = await CustomerOrgService(session, permissions, settings).set_archived(
        actor, organization_id, archived=False
    )
    await session.commit()
    return _organization(view)


@customers_router.get("/{organization_id}/members", response_model=list[CustomerMemberResponse])
async def list_customer_members(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
) -> list[CustomerMemberResponse]:
    users = await CustomerOrgService(session, permissions, settings).list_members(
        actor, organization_id
    )
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
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
    payload: MembershipRequest,
) -> CustomerMemberResponse:
    user = await CustomerOrgService(session, permissions, settings).add_member(
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
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    await CustomerOrgService(session, permissions, settings).remove_member(
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


@customers_router.post(
    "/{organization_id}/invitations",
    response_model=CustomerMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_customer(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    organization_id: UUID,
    payload: CustomerInviteRequest,
) -> CustomerMemberResponse:
    """고객을 초대하고 이 조직에 넣는다. 한 트랜잭션이다."""
    user = await CustomerOrgService(session, permissions, settings).invite_customer(
        actor, organization_id, email=payload.email, display_name=payload.display_name
    )
    await session.commit()
    return CustomerMemberResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        status="active" if user.is_active else "inactive",
    )


@customers_router.get("/suggest")
async def suggest_organization(
    session: DbSession,
    permissions: PermissionDep,
    settings: AppSettings,
    actor: CurrentActor,
    email: str,
) -> dict[str, str | None]:
    """이 주소의 도메인을 주장하는 조직. 화면의 기본값 **제안**이다."""
    found = await CustomerOrgService(session, permissions, settings).suggest_organization(
        actor, email
    )
    return {"organization_id": str(found) if found else None}


@portal_router.get("", response_model=list[PortalInfoResponse])
async def my_portals(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor
) -> list[PortalInfoResponse]:
    """이 설치의 창구 목록. **로그인해야 열린다** (`actor` 의존성이 그것이다).

    초대를 받아 비밀번호를 정한 고객이 앱 뿌리로 들어왔을 때 어디로 보낼지
    정하는 데 쓴다. 익명에게 열어 두면 설치된 창구를 아무나 훑는다.
    """
    portals = await CustomerPortalService(session, permissions).list_open_portals()
    return [
        PortalInfoResponse(
            slug=portal.slug,
            name=portal.name,
            description=portal.description,
            theme=portal.theme,
            allows_guests=portal.is_public,
        )
        for portal in portals
    ]


# ── 상담원이 보는 티켓 ─────────────────────────────────────────


@tickets_router.get("/{issue_id}", response_model=AgentTicketEnvelope)
async def agent_ticket(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, issue_id: UUID
) -> AgentTicketEnvelope:
    """이 이슈의 데스크 정보. **티켓이 아니면 `ticket: null` 이다.**

    200 으로 답하는 것이 중요하다. 상담원은 평범한 이슈를 하루에 수십 번
    열고, 그 때마다 404 가 찍히면 콘솔은 못 읽는 것이 된다 — 그러면 진짜
    오류가 가장 오래 살아남는다. 봉투로 감싸는 이유는 화면이 확인 없이
    그릴 수 없게 하려는 것이다: `ticket` 이 널 가능이므로 `tsc` 가 막는다.
    """
    view = await AgentTicketService(session, permissions).get(actor, issue_id)
    if view is None:
        return AgentTicketEnvelope(ticket=None)
    return AgentTicketEnvelope(
        ticket=AgentTicketResponse(
            issue_id=view.ticket.issue_id,
            channel=view.ticket.channel,
            request_type_name=view.request_type_name,
            portal_slug=view.portal_slug,
            requester=(
                RequesterResponse(
                    user_id=view.requester.user_id,
                    display_name=view.requester.display_name,
                    email=view.requester.email,
                    verified=view.requester.verified,
                )
                if view.requester
                else None
            ),
            organization_name=view.organization_name,
            csat_score=view.ticket.csat_score,
            sla=[
                SlaStandingResponse(
                    policy_name=row.policy_name,
                    metric=row.metric,
                    target_at=row.target_at,
                    remaining_seconds=row.remaining_seconds,
                    breached=row.breached,
                    paused=row.paused,
                    completed=row.completed,
                )
                for row in view.sla
            ],
        )
    )


# ── 고객의 대화 (C7) ───────────────────────────────────────────


@portal_router.get("/{slug}/requests/{issue_id}/replies", response_model=list[ReplyResponse])
async def portal_replies(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    slug: str,
    issue_id: UUID,
) -> list[ReplyResponse]:
    """이 요청의 대화. 내부 노트는 오지 않는다."""
    rows = await CustomerPortalService(session, permissions).list_replies(actor, slug, issue_id)
    return [
        ReplyResponse(
            id=row.id,
            author_id=row.author_id,
            body=row.body,
            created_at=row.created_at,
            edited_at=row.edited_at,
        )
        for row in rows
    ]


@portal_router.post(
    "/{slug}/requests/{issue_id}/replies",
    response_model=ReplyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def portal_reply(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    slug: str,
    issue_id: UUID,
    payload: ReplyRequest,
) -> ReplyResponse:
    row = await CustomerPortalService(session, permissions).reply(
        actor, slug, issue_id, payload.body
    )
    await session.commit()
    return ReplyResponse(
        id=row.id,
        author_id=row.author_id,
        body=row.body,
        created_at=row.created_at,
        edited_at=row.edited_at,
    )


# ── 큐 (C3) ────────────────────────────────────────────────────


def _queue(view: QueueView) -> QueueResponse:
    return QueueResponse(
        id=view.queue.id,
        project_id=view.queue.project_id,
        name=view.queue.name,
        iql=view.queue.iql,
        position=view.queue.position,
    )


@queues_router.get("", response_model=list[QueueResponse])
async def list_queues(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[QueueResponse]:
    """이 프로젝트의 큐. 순서대로 온다 — 사이드바가 그 순서로 그린다."""
    rows = await QueueService(session, permissions).list_for(actor, project_id)
    return [
        QueueResponse(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            iql=row.iql,
            position=row.position,
        )
        for row in rows
    ]


@queues_router.post("", response_model=QueueResponse, status_code=status.HTTP_201_CREATED)
async def create_queue(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: QueueCreateRequest,
) -> QueueResponse:
    view = await QueueService(session, permissions).create(
        actor,
        project_id=payload.project_id,
        name=payload.name,
        iql=payload.iql,
        position=payload.position,
    )
    await session.commit()
    return _queue(view)


@queues_router.patch("/{queue_id}", response_model=QueueResponse)
async def update_queue(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    queue_id: UUID,
    payload: QueueUpdateRequest,
) -> QueueResponse:
    view = await QueueService(session, permissions).update(
        actor, queue_id, name=payload.name, iql=payload.iql, position=payload.position
    )
    await session.commit()
    return _queue(view)


@queues_router.delete("/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, queue_id: UUID
) -> None:
    await QueueService(session, permissions).delete(actor, queue_id)
    await session.commit()


@queues_router.get("/{queue_id}/tickets", response_model=QueueTicketPageResponse)
async def run_queue(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    queue_id: UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> QueueTicketPageResponse:
    """큐를 돌린다. **실행자 권한으로** 돈다 — 큐가 권한을 넓히지 않는다."""
    page = await QueueService(session, permissions).run(
        actor, queue_id, PageRequest(limit=limit, cursor=cursor)
    )
    return QueueTicketPageResponse(
        items=[
            QueueTicketResponse(
                id=row.id,
                key=row.key,
                summary=row.summary,
                state_name=row.state_name,
                state_category=row.state_category,
                priority=row.priority,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        total=page.total,
    )


# ── 정형 응답 (C10) ────────────────────────────────────────────


def _canned(view: CannedResponseView) -> CannedResponseResponse:
    return CannedResponseResponse(
        id=view.response.id,
        project_id=view.response.project_id,
        name=view.response.name,
        body=view.response.body,
        shortcut=view.response.shortcut,
    )


@canned_router.get("", response_model=list[CannedResponseResponse])
async def list_canned_responses(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[CannedResponseResponse]:
    rows = await CannedResponseService(session, permissions).list_for(actor, project_id)
    return [
        CannedResponseResponse(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            body=row.body,
            shortcut=row.shortcut,
        )
        for row in rows
    ]


@canned_router.post("", response_model=CannedResponseResponse, status_code=status.HTTP_201_CREATED)
async def create_canned_response(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: CannedResponseCreateRequest,
) -> CannedResponseResponse:
    view = await CannedResponseService(session, permissions).create(
        actor,
        project_id=payload.project_id,
        name=payload.name,
        body=payload.body,
        shortcut=payload.shortcut,
    )
    await session.commit()
    return _canned(view)


@canned_router.patch("/{response_id}", response_model=CannedResponseResponse)
async def update_canned_response(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    response_id: UUID,
    payload: CannedResponseUpdateRequest,
) -> CannedResponseResponse:
    view = await CannedResponseService(session, permissions).update(
        actor,
        response_id,
        name=payload.name,
        body=payload.body,
        shortcut=payload.shortcut,
        clear_shortcut=payload.clear_shortcut,
    )
    await session.commit()
    return _canned(view)


@canned_router.delete("/{response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canned_response(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, response_id: UUID
) -> None:
    await CannedResponseService(session, permissions).delete(actor, response_id)
    await session.commit()


# ── 업무 달력 (C4) ─────────────────────────────────────────────


def _calendar(row: Any) -> CalendarResponse:
    return CalendarResponse(
        id=row.id,
        name=row.name,
        timezone=row.timezone,
        working_hours=dict(row.working_hours),
        holidays=list(row.holidays),
    )


@calendars_router.get("", response_model=list[CalendarResponse])
async def list_calendars(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor
) -> list[CalendarResponse]:
    rows = await SlaAdminService(session, permissions).list_calendars(actor)
    return [_calendar(row) for row in rows]


@calendars_router.post("", response_model=CalendarResponse, status_code=status.HTTP_201_CREATED)
async def create_calendar(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: CalendarCreateRequest,
) -> CalendarResponse:
    view = await SlaAdminService(session, permissions).create_calendar(
        actor,
        name=payload.name,
        timezone=payload.timezone,
        working_hours=payload.working_hours,
        holidays=payload.holidays,
    )
    await session.commit()
    return _calendar(view.calendar)


@calendars_router.patch("/{calendar_id}", response_model=CalendarResponse)
async def update_calendar(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    calendar_id: UUID,
    payload: CalendarUpdateRequest,
) -> CalendarResponse:
    view = await SlaAdminService(session, permissions).update_calendar(
        actor,
        calendar_id,
        name=payload.name,
        timezone=payload.timezone,
        working_hours=payload.working_hours,
        holidays=payload.holidays,
    )
    await session.commit()
    return _calendar(view.calendar)


@calendars_router.delete("/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, calendar_id: UUID
) -> None:
    await SlaAdminService(session, permissions).delete_calendar(actor, calendar_id)
    await session.commit()


# ── SLA 정책 (C4) ──────────────────────────────────────────────


def _policy(view: SlaPolicyView) -> SlaPolicyResponse:
    return SlaPolicyResponse(
        id=view.policy.id,
        project_id=view.policy.project_id,
        name=view.policy.name,
        metric=view.policy.metric,
        calendar_id=view.policy.calendar_id,
        calendar_name=view.calendar_name,
        goals=[dict(goal) for goal in view.policy.goals],
        pause_state_ids=list(view.policy.pause_state_ids),
        is_enabled=view.policy.is_enabled,
    )


@sla_router.get("", response_model=list[SlaPolicyResponse])
async def list_sla_policies(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[SlaPolicyResponse]:
    rows = await SlaAdminService(session, permissions).list_policies(actor, project_id)
    return [_policy(row) for row in rows]


@sla_router.post("", response_model=SlaPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_sla_policy(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: SlaPolicyCreateRequest,
) -> SlaPolicyResponse:
    view = await SlaAdminService(session, permissions).create_policy(
        actor,
        project_id=payload.project_id,
        name=payload.name,
        metric=payload.metric,
        calendar_id=payload.calendar_id,
        goals=payload.goals,
        pause_state_ids=payload.pause_state_ids,
    )
    await session.commit()
    return _policy(view)


@sla_router.patch("/{policy_id}", response_model=SlaPolicyResponse)
async def update_sla_policy(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    policy_id: UUID,
    payload: SlaPolicyUpdateRequest,
) -> SlaPolicyResponse:
    view = await SlaAdminService(session, permissions).update_policy(
        actor,
        policy_id,
        name=payload.name,
        calendar_id=payload.calendar_id,
        goals=payload.goals,
        pause_state_ids=payload.pause_state_ids,
        is_enabled=payload.is_enabled,
    )
    await session.commit()
    return _policy(view)


@sla_router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sla_policy(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, policy_id: UUID
) -> None:
    await SlaAdminService(session, permissions).delete_policy(actor, policy_id)
    await session.commit()
