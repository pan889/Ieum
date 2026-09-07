"""포털·요청 유형·고객 조직 (M4 첫 조각, feature-map C1·C2·C13).

여기서 고정하는 것은 세 가지다:

1. **저장은 되고 제출이 실패하는 폼**을 만들 수 없다. 고객은 폼을 다 채우고
   마지막에 실패를 본다 — 가장 나쁜 자리에서 드러나는 결함이다.
2. **고객은 자기 것과 자기 조직 것만 본다.** 특히 조직이 없는 고객이 조직
   미지정 티켓 전부를 보게 되는 실수를 막는다.
3. **게스트 요청이 남의 계정에 붙지 않는다.** 주소는 검증되지 않은 값이다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as perms
from ieum.modules.desk.models import TicketExt
from ieum.modules.desk.service import CustomerOrgService, CustomerPortalService, PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import (
    FieldDefinition,
    Issue,
    IssueType,
    Workflow,
    WorkflowState,
)
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    # `issues.contracts.get_tickets` 는 전역 권한 서비스를 집어 쓴다 —
    # 목록의 범위는 ticket_ext 쪽에서 이미 좁혀졌기 때문이다. 그 전역이
    # 비어 있으면 목록 조회가 그 자리에서 죽는다.
    set_permission_service(service)
    return service


async def _person(session: AsyncSession, *, customer: bool = False) -> User:
    row = User(
        email=f"p-{new_id()}@example.com",
        display_name="Person",
        status="active",
        is_customer=customer,
    )
    session.add(row)
    await session.flush()
    return row


def _customer_actor(user: User) -> Actor:
    return Actor(user_id=user.id, email=user.email, is_customer=True, is_active=True)


async def _project(session: AsyncSession) -> Project:
    # **꼬리를 쓴다.** UUIDv7 은 앞이 타임스탬프라 같은 밀리초에 만든 두 id
    # 의 앞 6자리가 같다 — `hex[:6]` 으로 자르면 한 테스트에서 프로젝트를
    # 둘 만드는 순간 키가 충돌한다(실제로 그랬다).
    row = Project(key=f"D{new_id().hex[-6:].upper()}", name="Desk")
    session.add(row)
    await session.flush()
    return row


async def _issue_type(session: AsyncSession, project: Project | None = None) -> IssueType:
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
    )
    row = IssueType(
        project_id=project.id if project else None,
        name="Request",
        workflow_id=workflow.id,
    )
    session.add(row)
    await session.flush()
    return row


async def _field(
    session: AsyncSession,
    *,
    key: str,
    kind: str = "text",
    required: bool = False,
    project: Project | None = None,
    issue_type: IssueType | None = None,
    config: dict[str, object] | None = None,
) -> FieldDefinition:
    row = FieldDefinition(
        key=key,
        name=key.title(),
        kind=kind,
        is_required=required,
        project_id=project.id if project else None,
        issue_type_id=issue_type.id if issue_type else None,
        config=config or {},
    )
    session.add(row)
    await session.flush()
    return row


async def _manager(session: AsyncSession, project: Project) -> User:
    person = await _person(session)
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=(perms.PORTAL_MANAGE,),
        scope=Scope.project(project.id),
    )
    return person


async def _customer_admin(session: AsyncSession) -> User:
    person = await _person(session)
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=(perms.CUSTOMER_MANAGE,),
        scope=Scope.global_(),
    )
    return person


def _orgs(
    session: AsyncSession, permissions: PermissionService, settings: Settings
) -> CustomerOrgService:
    """고객 조직 서비스. `settings` 를 드는 이유는 초대가 계정을 만들기
    때문이다 — identity 의 초대가 비밀번호 정책 파라미터를 본다."""
    return CustomerOrgService(session, permissions, settings)


SUMMARY_FIELD = {"key": "summary", "label": "무엇이 필요하신가요", "required": True}
BODY_FIELD = {"key": "description", "label": "자세히", "required": False}


class TestPortalPermission:
    async def test_a_stranger_cannot_list_portals(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await PortalService(session, permissions).list_for_project(
                actor_for(nobody), project.id
            )

    async def test_a_stranger_cannot_create_a_portal(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await PortalService(session, permissions).create(
                actor_for(nobody),
                project_id=project.id,
                name="Help",
                slug="help",
                description=None,
                theme={},
                is_public=False,
            )


class TestPortalDefinition:
    async def test_a_manager_creates_one(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        view = await PortalService(session, permissions).create(
            actor_for(manager),
            project_id=project.id,
            name="Help Center",
            slug="Help-Center",
            description="  ",
            theme={"color": "#123456"},
            is_public=True,
        )
        assert view.portal.slug == "help-center", "슬러그는 소문자로 굳는다"
        # 빈 칸만 있는 설명은 None 이다. 빈 문자열로 남기면 화면이 "설명
        # 있음" 으로 읽어 빈 줄을 그린다.
        assert view.portal.description is None
        assert view.portal.is_public is True

    @pytest.mark.parametrize("slug", ["-bad", "bad-", "Bad Slug", "a", "b_c"])
    async def test_bad_slugs_are_refused(
        self, session: AsyncSession, permissions: PermissionService, slug: str
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await PortalService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="Help",
                slug=slug,
                description=None,
                theme={},
                is_public=False,
            )
        assert exc.value.code == "desk.invalid_slug"

    async def test_the_slug_is_unique(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        service = PortalService(session, permissions)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="A",
            slug="shared",
            description=None,
            theme={},
            is_public=False,
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="B",
                slug="SHARED",
                description=None,
                theme={},
                is_public=False,
            )
        assert exc.value.code == "desk.portal_slug_taken"

    async def test_an_archived_portal_is_invisible_to_customers(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """접었는데 여전히 응답하면 접은 것이 아니다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = PortalService(session, permissions)
        view = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug="folded",
            description=None,
            theme={},
            is_public=True,
        )
        await service.set_archived(actor_for(manager), view.portal.id, archived=True)
        with pytest.raises(NotFoundError):
            await CustomerPortalService(session, permissions).info("folded")

        await service.set_archived(actor_for(manager), view.portal.id, archived=False)
        assert (await CustomerPortalService(session, permissions).info("folded")).name == "Help"


async def _portal_with_form(
    session: AsyncSession,
    permissions: PermissionService,
    *,
    slug: str = "help",
    is_public: bool = False,
    extra_fields: list[dict[str, object]] | None = None,
    mapping: dict[str, str] | None = None,
) -> tuple[Project, User, object, object]:
    project = await _project(session)
    manager = await _manager(session, project)
    issue_type = await _issue_type(session, project)
    service = PortalService(session, permissions)
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=slug,
            description=None,
            theme={},
            is_public=is_public,
        )
    ).portal
    request_type = (
        await service.create_request_type(
            actor_for(manager),
            portal.id,
            issue_type_id=issue_type.id,
            name="Broken thing",
            description=None,
            icon=None,
            position=0,
            form_fields=[SUMMARY_FIELD, BODY_FIELD, *(extra_fields or [])],
            field_mapping=mapping or {},
            is_enabled=True,
        )
    ).request_type
    return project, manager, portal, request_type


class TestFormValidation:
    """저장되면 제출도 되어야 한다. 그 계약을 여기서 고정한다."""

    async def test_a_form_without_a_summary_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        issue_type = await _issue_type(session, project)
        with pytest.raises(ValidationError) as exc:
            await PortalService(session, permissions).create_request_type(
                actor_for(manager),
                (
                    await PortalService(session, permissions).create(
                        actor_for(manager),
                        project_id=project.id,
                        name="Help",
                        slug="nosummary",
                        description=None,
                        theme={},
                        is_public=False,
                    )
                ).portal.id,
                issue_type_id=issue_type.id,
                name="No summary",
                description=None,
                icon=None,
                position=0,
                form_fields=[BODY_FIELD],
                field_mapping={},
                is_enabled=True,
            )
        assert exc.value.code == "desk.form_needs_summary"

    async def test_an_unmapped_field_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """갈 곳 없는 답은 버려지는 답이고, 고객은 그것을 알 수 없다."""
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug="unmapped",
                extra_fields=[{"key": "device", "label": "기기"}],
                mapping={},
            )
        assert exc.value.code == "desk.form_field_unmapped"
        assert exc.value.details == {"keys": ["device"]}

    async def test_a_mapping_to_a_missing_definition_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug="ghost",
                extra_fields=[{"key": "device", "label": "기기"}],
                mapping={"device": "no_such_field"},
            )
        assert exc.value.code == "desk.unknown_field_definition"

    async def test_a_dangling_mapping_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        await _field(session, key="device")
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session, permissions, slug="dangling", mapping={"device": "device"}
            )
        assert exc.value.code == "desk.mapping_without_field"

    async def test_two_fields_cannot_share_one_target(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """마지막 값이 조용히 이긴다. 그러면 앞 답이 사라진다."""
        await _field(session, key="device")
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug="collide",
                extra_fields=[
                    {"key": "a", "label": "A"},
                    {"key": "b", "label": "B"},
                ],
                mapping={"a": "device", "b": "device"},
            )
        assert exc.value.code == "desk.duplicate_mapping_target"

    async def test_a_reserved_key_cannot_be_mapped(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        await _field(session, key="device")
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session, permissions, slug="reserved", mapping={"summary": "device"}
            )
        assert exc.value.code == "desk.reserved_key_mapped"

    async def test_a_required_definition_must_be_on_the_form(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """폼에 없으면 제출이 **언제나** 실패한다. 저장 시점에 거절한다."""
        project = await _project(session)
        manager = await _manager(session, project)
        issue_type = await _issue_type(session, project)
        await _field(session, key="must", required=True, project=project)
        portal = (
            await PortalService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="Help",
                slug="required",
                description=None,
                theme={},
                is_public=False,
            )
        ).portal
        with pytest.raises(ValidationError) as exc:
            await PortalService(session, permissions).create_request_type(
                actor_for(manager),
                portal.id,
                issue_type_id=issue_type.id,
                name="Missing required",
                description=None,
                icon=None,
                position=0,
                form_fields=[SUMMARY_FIELD],
                field_mapping={},
                is_enabled=True,
            )
        assert exc.value.code == "desk.required_field_missing_from_form"
        assert exc.value.details == {"keys": ["must"]}

    async def test_an_issue_type_from_another_project_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """폼은 저장되고 제출이 실패하는 자리다."""
        project = await _project(session)
        other = await _project(session)
        manager = await _manager(session, project)
        foreign = await _issue_type(session, other)
        portal = (
            await PortalService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="Help",
                slug="foreign",
                description=None,
                theme={},
                is_public=False,
            )
        ).portal
        with pytest.raises(ValidationError) as exc:
            await PortalService(session, permissions).create_request_type(
                actor_for(manager),
                portal.id,
                issue_type_id=foreign.id,
                name="Foreign",
                description=None,
                icon=None,
                position=0,
                form_fields=[SUMMARY_FIELD],
                field_mapping={},
                is_enabled=True,
            )
        assert exc.value.code == "desk.issue_type_not_available"

    async def test_duplicate_keys_are_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        await _field(session, key="device")
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug="dupkey",
                extra_fields=[
                    {"key": "device", "label": "A"},
                    {"key": "device", "label": "B"},
                ],
                mapping={"device": "device"},
            )
        assert exc.value.code == "desk.duplicate_form_key"

    async def test_a_field_needs_a_label(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        await _field(session, key="device")
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug="nolabel",
                extra_fields=[{"key": "device", "label": "   "}],
                mapping={"device": "device"},
            )
        assert exc.value.code == "desk.form_field_needs_label"


class TestPortalForm:
    async def test_the_form_carries_the_kind_from_the_definition(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """폼은 종류를 저장하지 않는다. 두 벌이면 어긋나기 때문이다."""
        await _field(
            session,
            key="device",
            kind="select",
            config={"options": ["laptop", "phone"]},
        )
        _, _, _, request_type = await _portal_with_form(
            session,
            permissions,
            slug="kinds",
            extra_fields=[{"key": "device", "label": "기기", "required": True}],
            mapping={"device": "device"},
        )
        form = await CustomerPortalService(session, permissions).get_form(
            "kinds",
            request_type.id,  # type: ignore[attr-defined]
        )
        by_key = {f.key: f for f in form.fields}
        assert by_key["summary"].kind == "text"
        assert by_key["description"].kind == "markdown"
        assert by_key["device"].kind == "select"
        assert by_key["device"].config == {"options": ["laptop", "phone"]}

    async def test_the_summary_is_required_whatever_the_form_says(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """끌 수 있게 두면 제목 없는 티켓이 큐에 쌓인다."""
        project = await _project(session)
        manager = await _manager(session, project)
        issue_type = await _issue_type(session, project)
        portal = (
            await PortalService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="Help",
                slug="optsummary",
                description=None,
                theme={},
                is_public=False,
            )
        ).portal
        request_type = (
            await PortalService(session, permissions).create_request_type(
                actor_for(manager),
                portal.id,
                issue_type_id=issue_type.id,
                name="Optional summary",
                description=None,
                icon=None,
                position=0,
                form_fields=[{"key": "summary", "label": "제목", "required": False}],
                field_mapping={},
                is_enabled=True,
            )
        ).request_type
        form = await CustomerPortalService(session, permissions).get_form(
            "optsummary", request_type.id
        )
        assert next(f for f in form.fields if f.key == "summary").required is True

    async def test_a_disabled_type_is_not_offered(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, manager, _portal, request_type = await _portal_with_form(
            session, permissions, slug="disabled"
        )
        await PortalService(session, permissions).update_request_type(
            actor_for(manager),
            request_type.id,  # type: ignore[attr-defined]
            is_enabled=False,
        )
        _, offered = await CustomerPortalService(session, permissions).list_forms("disabled")
        assert offered == []
        with pytest.raises(NotFoundError):
            await CustomerPortalService(session, permissions).get_form(
                "disabled",
                request_type.id,  # type: ignore[attr-defined]
            )


class TestSubmit:
    async def test_a_customer_submits_and_gets_a_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객은 `issue.create` 를 갖지 않는다. 그래도 통과해야 한다."""
        await _field(session, key="device")
        _, _, _, request_type = await _portal_with_form(
            session,
            permissions,
            slug="submit",
            extra_fields=[{"key": "device", "label": "기기"}],
            mapping={"device": "device"},
        )
        customer = await _person(session, customer=True)
        view = await CustomerPortalService(session, permissions).submit(
            _customer_actor(customer),
            "submit",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "안 켜집니다", "description": "본체", "device": "laptop"},
        )
        assert view.issue.summary == "안 켜집니다"
        # 답은 **폼 키**로 되돌아온다. 커스텀 필드 키를 고객에게 보이지 않는다.
        # 라벨도 함께 온다 — 화면이 키만 받으면 고객에게 `device` 를 보여 준다.
        assert [(a.key, a.label, a.value) for a in view.answers] == [("device", "기기", "laptop")]
        assert view.ticket.channel == "portal"
        assert view.ticket.reporter_customer_id == customer.id

    async def test_an_unknown_answer_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """조용히 버리면 고객은 적은 내용이 사라진 것을 모른다."""
        _, _, _, request_type = await _portal_with_form(session, permissions, slug="unknown")
        customer = await _person(session, customer=True)
        with pytest.raises(ValidationError) as exc:
            await CustomerPortalService(session, permissions).submit(
                _customer_actor(customer),
                "unknown",
                request_type_id=request_type.id,  # type: ignore[attr-defined]
                answers={"summary": "제목", "nope": "값"},
            )
        assert exc.value.code == "desk.unknown_answer"

    async def test_a_required_answer_cannot_be_blank(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        await _field(session, key="device")
        _, _, _, request_type = await _portal_with_form(
            session,
            permissions,
            slug="blank",
            extra_fields=[{"key": "device", "label": "기기", "required": True}],
            mapping={"device": "device"},
        )
        customer = await _person(session, customer=True)
        with pytest.raises(ValidationError) as exc:
            await CustomerPortalService(session, permissions).submit(
                _customer_actor(customer),
                "blank",
                request_type_id=request_type.id,  # type: ignore[attr-defined]
                answers={"summary": "제목", "device": "   "},
            )
        assert exc.value.code == "desk.answer_required"

    async def test_a_guest_cannot_submit_to_a_private_portal(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, _, _, request_type = await _portal_with_form(
            session, permissions, slug="private", is_public=False
        )
        with pytest.raises(PermissionDeniedError) as exc:
            await CustomerPortalService(session, permissions).submit_as_guest(
                "private",
                request_type_id=request_type.id,  # type: ignore[attr-defined]
                email="stranger@example.com",
                name="Stranger",
                answers={"summary": "제목"},
            )
        assert exc.value.code == "desk.guest_not_allowed"

    async def test_a_guest_ticket_has_no_reporter(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """증명 없이 남의 이름으로 티켓을 만드는 일이 되면 안 된다.

        같은 주소의 계정이 **이미 있어도** 붙이지 않는다.
        """
        existing = await _person(session, customer=True)
        _, _, _, request_type = await _portal_with_form(
            session, permissions, slug="guest", is_public=True
        )
        view = await CustomerPortalService(session, permissions).submit_as_guest(
            "guest",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            email=existing.email.upper(),
            name="  Guest  ",
            answers={"summary": "게스트 요청"},
        )
        assert view.ticket.reporter_customer_id is None
        assert view.ticket.guest_email == existing.email.lower()
        assert view.ticket.guest_name == "Guest"
        # 이슈의 신고자도 비어 있어야 한다 — 검증되지 않은 주소를 신고자
        # 자리에 올리지 않는다.
        issue = await session.get(Issue, view.issue.id)
        assert issue is not None
        assert issue.reporter_id is None

    @pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@example.com", "a b@example.com"])
    async def test_a_guest_address_must_look_like_one(
        self, session: AsyncSession, permissions: PermissionService, bad: str
    ) -> None:
        _, _, _, request_type = await _portal_with_form(
            session, permissions, slug="badmail", is_public=True
        )
        with pytest.raises(ValidationError) as exc:
            await CustomerPortalService(session, permissions).submit_as_guest(
                "badmail",
                request_type_id=request_type.id,  # type: ignore[attr-defined]
                email=bad,
                name="Guest",
                answers={"summary": "제목"},
            )
        assert exc.value.code == "desk.invalid_email"


class TestVisibility:
    """고객은 자기 것과 자기 조직 것만 본다 (auth.md 5절)."""

    async def test_a_customer_without_an_organization_sees_only_their_own(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`organization_id IS NULL` 을 조건에 넣으면 조직 없는 고객이
        **조직 미지정 티켓 전부**를 본다. 그 실수를 고정한다."""
        _, _, _, request_type = await _portal_with_form(session, permissions, slug="vis")
        mine = await _person(session, customer=True)
        theirs = await _person(session, customer=True)
        service = CustomerPortalService(session, permissions)
        await service.submit(
            _customer_actor(mine),
            "vis",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "내 것"},
        )
        await service.submit(
            _customer_actor(theirs),
            "vis",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "남의 것"},
        )
        page = await service.list_my_tickets(_customer_actor(mine), "vis", PageRequest(limit=50))
        assert [v.issue.summary for v in page.items] == ["내 것"]

    async def test_the_same_organization_sees_each_other(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        _, _, _, request_type = await _portal_with_form(session, permissions, slug="org")
        admin = await _customer_admin(session)
        colleague_a = await _person(session, customer=True)
        colleague_b = await _person(session, customer=True)
        outsider = await _person(session, customer=True)
        orgs = CustomerOrgService(session, permissions, settings)
        school = (
            await orgs.create(actor_for(admin), name="한빛초등학교", domains=[], note=None)
        ).organization
        await orgs.add_member(actor_for(admin), school.id, user_id=colleague_a.id)
        await orgs.add_member(actor_for(admin), school.id, user_id=colleague_b.id)

        service = CustomerPortalService(session, permissions)
        await service.submit(
            _customer_actor(colleague_a),
            "org",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "동료 A 의 요청"},
        )
        await service.submit(
            _customer_actor(outsider),
            "org",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "외부인의 요청"},
        )
        page = await service.list_my_tickets(
            _customer_actor(colleague_b), "org", PageRequest(limit=50)
        )
        assert [v.issue.summary for v in page.items] == ["동료 A 의 요청"]

    async def test_another_customers_ticket_is_a_404_not_a_403(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """남의 티켓이 **있다는 사실**도 알려 주지 않는다."""
        _, _, _, request_type = await _portal_with_form(session, permissions, slug="hide")
        mine = await _person(session, customer=True)
        theirs = await _person(session, customer=True)
        service = CustomerPortalService(session, permissions)
        other = await service.submit(
            _customer_actor(theirs),
            "hide",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "남의 것"},
        )
        with pytest.raises(NotFoundError):
            await service.get_my_ticket(_customer_actor(mine), "hide", other.issue.id)

    async def test_tickets_from_another_portal_do_not_leak_in(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, _, _, first = await _portal_with_form(session, permissions, slug="one")
        _, _, _, second = await _portal_with_form(session, permissions, slug="two")
        customer = await _person(session, customer=True)
        service = CustomerPortalService(session, permissions)
        await service.submit(
            _customer_actor(customer),
            "one",
            request_type_id=first.id,  # type: ignore[attr-defined]
            answers={"summary": "첫 창구"},
        )
        await service.submit(
            _customer_actor(customer),
            "two",
            request_type_id=second.id,  # type: ignore[attr-defined]
            answers={"summary": "둘째 창구"},
        )
        page = await service.list_my_tickets(
            _customer_actor(customer), "one", PageRequest(limit=50)
        )
        assert [v.issue.summary for v in page.items] == ["첫 창구"]


class TestCustomerOrganizations:
    async def test_a_stranger_cannot_list_them(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await CustomerOrgService(session, permissions, settings).list_all(
                actor_for(nobody), PageRequest(limit=10)
            )

    async def test_domains_are_normalized(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        admin = await _customer_admin(session)
        view = await CustomerOrgService(session, permissions, settings).create(
            actor_for(admin),
            name="한빛초",
            domains=["@HANBIT.example.com ", "hanbit.example.com", " other.example.org"],
            note=None,
        )
        # `@` 를 떼고, 소문자로 굳히고, 중복은 하나로.
        assert view.organization.domains == ["hanbit.example.com", "other.example.org"]

    @pytest.mark.parametrize("bad", ["nodot", "has space.com"])
    async def test_a_bad_domain_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings, bad: str
    ) -> None:
        admin = await _customer_admin(session)
        with pytest.raises(ValidationError) as exc:
            await CustomerOrgService(session, permissions, settings).create(
                actor_for(admin), name=f"org-{new_id()}", domains=[bad], note=None
            )
        assert exc.value.code == "desk.invalid_domain"

    async def test_an_internal_account_cannot_join(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """내부 계정에 조직 가시성이 붙으면 '고객 조직' 이라는 개념이 흐려진다."""
        admin = await _customer_admin(session)
        insider = await _person(session, customer=False)
        service = CustomerOrgService(session, permissions, settings)
        org = (
            await service.create(actor_for(admin), name=f"org-{new_id()}", domains=[], note=None)
        ).organization
        with pytest.raises(ConflictError) as exc:
            await service.add_member(actor_for(admin), org.id, user_id=insider.id)
        assert exc.value.code == "desk.not_a_customer"

    async def test_one_customer_belongs_to_one_organization(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """두 번째 소속은 갈아 끼운다. 겹치면 티켓이 어느 조직 것인지 묻게 된다."""
        admin = await _customer_admin(session)
        customer = await _person(session, customer=True)
        service = CustomerOrgService(session, permissions, settings)
        first = (
            await service.create(actor_for(admin), name=f"a-{new_id()}", domains=[], note=None)
        ).organization
        second = (
            await service.create(actor_for(admin), name=f"b-{new_id()}", domains=[], note=None)
        ).organization
        await service.add_member(actor_for(admin), first.id, user_id=customer.id)
        await service.add_member(actor_for(admin), second.id, user_id=customer.id)
        assert [u.id for u in await service.list_members(actor_for(admin), first.id)] == []
        assert [u.id for u in await service.list_members(actor_for(admin), second.id)] == [
            customer.id
        ]

    async def test_removing_from_the_wrong_organization_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """실수로 남의 조직을 비우게 하지 않는다."""
        admin = await _customer_admin(session)
        customer = await _person(session, customer=True)
        service = CustomerOrgService(session, permissions, settings)
        mine = (
            await service.create(actor_for(admin), name=f"m-{new_id()}", domains=[], note=None)
        ).organization
        other = (
            await service.create(actor_for(admin), name=f"o-{new_id()}", domains=[], note=None)
        ).organization
        await service.add_member(actor_for(admin), mine.id, user_id=customer.id)
        with pytest.raises(ConflictError) as exc:
            await service.remove_member(actor_for(admin), other.id, user_id=customer.id)
        assert exc.value.code == "desk.not_a_member"

    async def test_archiving_keeps_the_memberships(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """소속을 끊으면 같은 기관 동료가 보던 티켓이 조용히 사라진다."""
        admin = await _customer_admin(session)
        customer = await _person(session, customer=True)
        service = CustomerOrgService(session, permissions, settings)
        org = (
            await service.create(actor_for(admin), name=f"k-{new_id()}", domains=[], note=None)
        ).organization
        await service.add_member(actor_for(admin), org.id, user_id=customer.id)
        await service.set_archived(actor_for(admin), org.id, archived=True)
        assert [u.id for u in await service.list_members(actor_for(admin), org.id)] == [customer.id]


class TestGuestOrganization:
    async def test_a_guest_lands_in_the_organization_that_claims_the_domain(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        admin = await _customer_admin(session)
        org = (
            await CustomerOrgService(session, permissions, settings).create(
                actor_for(admin), name="한빛초", domains=["hanbit.example.com"], note=None
            )
        ).organization
        _, _, _, request_type = await _portal_with_form(
            session, permissions, slug="bydomain", is_public=True
        )
        view = await CustomerPortalService(session, permissions).submit_as_guest(
            "bydomain",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            email="teacher@hanbit.example.com",
            name="교사",
            answers={"summary": "프린터"},
        )
        assert view.ticket.organization_id == org.id

    async def test_an_unclaimed_domain_lands_nowhere(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, _, _, request_type = await _portal_with_form(
            session, permissions, slug="nodomain", is_public=True
        )
        view = await CustomerPortalService(session, permissions).submit_as_guest(
            "nodomain",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            email="someone@unknown.example.net",
            name="누구",
            answers={"summary": "질문"},
        )
        assert view.ticket.organization_id is None


class TestTicketExtension:
    async def test_the_extension_dies_with_the_issue(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`ticket_ext` 가 남으면 목록이 이슈 없는 행을 그리려 한다."""
        _, _, _, request_type = await _portal_with_form(session, permissions, slug="cascade")
        customer = await _person(session, customer=True)
        view = await CustomerPortalService(session, permissions).submit(
            _customer_actor(customer),
            "cascade",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            answers={"summary": "지워질 것"},
        )
        issue = await session.get(Issue, view.issue.id)
        assert issue is not None
        await session.delete(issue)
        await session.flush()
        rows = (
            (await session.execute(select(TicketExt).where(TicketExt.issue_id == view.issue.id)))
            .scalars()
            .all()
        )
        assert rows == []


class TestFormKindsCustomersCannotSee:
    """선택지가 내부 데이터인 종류는 폼에 올릴 수 없다."""

    @pytest.mark.parametrize("kind", ["user", "version"])
    async def test_the_kind_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        settings: Settings,
        kind: str,
    ) -> None:
        """막지 않으면 포털이 그 필드를 그릴 방법이 없어서 **화면에 없는
        필수 항목**이 생긴다 — 고객은 다 채웠는데 제출이 거절된다."""
        await _field(session, key="who", kind=kind)
        with pytest.raises(ValidationError) as exc:
            await _portal_with_form(
                session,
                permissions,
                slug=f"kind{kind[:3]}",
                extra_fields=[{"key": "who", "label": "누구"}],
                mapping={"who": "who"},
            )
        assert exc.value.code == "desk.field_kind_not_on_forms"


class TestCreatingCustomers:
    """`is_customer` 를 켜는 길. 한동안 **아무도 켜지 못했다.**

    읽는 자리만 있고 쓰는 자리가 없어서, 고객 격리도 고객 조직도 고객 포털도
    만들어 두었는데 고객 계정을 만들 길이 없었다.
    """

    async def test_an_invited_customer_lands_in_the_organization(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """초대와 소속이 **한 트랜잭션**이다. 소속을 워커에 맡기면 아웃박스가
        훑기 전까지 소속 없는 고객이 존재하고, 그 사이 티켓은 조직 가시성을
        잃는다."""
        admin = await _customer_admin(session)
        service = _orgs(session, permissions, settings)
        org = (
            await service.create(actor_for(admin), name=f"i-{new_id()}", domains=[], note=None)
        ).organization
        user = await service.invite_customer(
            actor_for(admin),
            org.id,
            email="NEW-teacher@school.example.com",
            display_name="  새 담당자  ",
        )
        assert user.is_customer is True, "이 통로로 만든 계정은 언제나 고객이다"
        assert user.email == "new-teacher@school.example.com"
        assert user.display_name == "새 담당자"
        assert [u.id for u in await service.list_members(actor_for(admin), org.id)] == [user.id]

    async def test_a_stranger_cannot_invite(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        admin = await _customer_admin(session)
        nobody = await _person(session)
        org = (
            await _orgs(session, permissions, settings).create(
                actor_for(admin), name=f"j-{new_id()}", domains=[], note=None
            )
        ).organization
        with pytest.raises(PermissionDeniedError):
            await _orgs(session, permissions, settings).invite_customer(
                actor_for(nobody), org.id, email="x@y.example.com", display_name="X"
            )

    @pytest.mark.parametrize("bad", ["nope", "a@b", "no at sign"])
    async def test_a_bad_address_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        settings: Settings,
        bad: str,
    ) -> None:
        admin = await _customer_admin(session)
        org = (
            await _orgs(session, permissions, settings).create(
                actor_for(admin), name=f"k-{new_id()}", domains=[], note=None
            )
        ).organization
        with pytest.raises(ValidationError) as exc:
            await _orgs(session, permissions, settings).invite_customer(
                actor_for(admin), org.id, email=bad, display_name="X"
            )
        assert exc.value.code == "desk.invalid_email"

    async def test_the_domain_only_suggests(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """도메인은 **제안**이다. 소속을 만들지 않는다 — 사람이 확인한
        소속이라야 ACL 이 사람의 결정으로 남는다."""
        admin = await _customer_admin(session)
        service = _orgs(session, permissions, settings)
        org = (
            await service.create(
                actor_for(admin), name=f"l-{new_id()}", domains=["hint.example.com"], note=None
            )
        ).organization
        assert (
            await service.suggest_organization(actor_for(admin), "who@hint.example.com")
        ) == org.id
        assert (
            await service.suggest_organization(actor_for(admin), "who@other.example.com")
        ) is None


class TestPortalIndex:
    async def test_it_lists_open_portals_only(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """접힌 창구는 목록에도 없다. 있으면 눌러서 404 를 보게 된다."""
        _, manager, portal, _ = await _portal_with_form(session, permissions, slug="listed")
        service = CustomerPortalService(session, permissions)
        assert "listed" in [p.slug for p in await service.list_open_portals()]

        await PortalService(session, permissions).set_archived(
            actor_for(manager),
            portal.id,  # type: ignore[attr-defined]
            archived=True,
        )
        assert "listed" not in [p.slug for p in await service.list_open_portals()]
