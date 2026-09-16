"""메일 채널 정의의 저장 경로 (feature-map C6).

붙잡는 것:

- **비밀번호를 되돌려주지 않는다.** `has_password` 만 준다 — 값을 되돌려주면
  그것이 브라우저의 메모리·로그·오류 보고를 거쳐 다니게 된다.
- **비밀번호는 JSONB 밖에 있다.** 설정 사전에 넣으면 그 사전이 응답과 로그에
  그대로 실린다.
- **접속할 수 없는 설정은 저장 전에 거절한다.** 저장되고 폴링만 실패하면 그
  실패는 `last_error` 로만 보이고, 관리자는 저장이 성공했으니 됐다고 믿는다.
- **비밀번호를 안 보내면 그대로 둔다.** 폼은 그 칸을 비워 두고 저장하므로,
  빈 값을 지우기로 읽으면 이름만 고쳐도 메일 수신이 멈춘다.
- **다른 프로젝트의 요청 유형은 못 고른다.** 그 티켓은 이 프로젝트의 큐에 안
  걸린다 — 메일은 들어왔는데 아무도 못 본다.
- step-up 없이는 손도 못 댄다. 이 손잡이는 메일함 비밀번호를 받는다.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

#: `settings` 픽스처는 conftest 가 **세션 스코프**로 준다. 여기서 다시
#: 정의하면 함수 스코프가 되어 `engine`(세션 스코프)이 그것을 못 받고,
#: 모든 시험이 `ScopeMismatch` 로 죽는다 — 실제로 그랬다.
from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StepUpRequiredError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.service import EmailChannelService, PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

GOOD_INBOUND = {"host": "imap.example", "user": "help", "port": 993, "folder": "INBOX"}
PASSWORD = "mailbox-secret-1234"

SUMMARY_FIELD = {"key": "summary", "label": "무엇이 문제인가요", "required": True}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _setup(
    session: AsyncSession, permissions: PermissionService
) -> tuple[Project, User, object]:
    """프로젝트·관리자·요청 유형 한 벌."""
    project = Project(key=f"C{new_id().hex[-6:].upper()}", name="Email")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
    )
    issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    manager = User(email=f"m-{new_id()}@example.com", display_name="관리자", status="active")
    session.add(manager)
    await session.flush()
    await grant(
        session,
        principal_id=manager.id,
        permissions_granted=(desk_perms.PORTAL_MANAGE, desk_perms.EMAIL_MANAGE),
        scope=Scope.project(project.id),
    )
    service = PortalService(session, permissions)
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=f"help-{new_id().hex[-6:]}",
            description=None,
            theme={},
            is_public=True,
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
            form_fields=[SUMMARY_FIELD],
            field_mapping={},
            is_enabled=True,
        )
    ).request_type
    return project, manager, request_type


class TestArchivingFreesTheAddress:
    async def test_the_same_address_can_be_used_again(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """접은 채널의 주소는 **다시 걸 수 있어야 한다.**

        주소가 DB 에서 통째로 유일해서, 한 번 쓴 `help@` 는 채널을 접어도
        영영 다시 못 걸었다. 창구를 접었다 다시 여는 것은 흔한 일이다.

        배달이 갈리지 않는 이유: 수신 폴러(`worker/tasks.py`)도 발신
        고르기(`outbound.py`)도 원래부터 살아 있는 채널만 본다.
        """
        project, manager, request_type = await _setup(session, permissions)
        service = EmailChannelService(session, permissions, settings)
        body = {
            "project_id": project.id,
            "outbound_from": "help@ours.example",
            "inbound": dict(GOOD_INBOUND),
            "password": PASSWORD,
            "default_request_type_id": request_type.id,
        }
        first = await service.create(actor_for(manager), address="help@ours.example", **body)  # type: ignore[arg-type]
        await service.delete(actor_for(manager), first.channel.id)

        again = await service.create(actor_for(manager), address="help@ours.example", **body)  # type: ignore[arg-type]
        assert again.channel.id != first.channel.id
        assert again.channel.address == "help@ours.example"

    async def test_a_live_address_is_still_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """두 채널이 같은 주소를 들고 있으면 들어온 메일이 어디로 갈지
        갈린다. 살아 있는 것끼리는 여전히 막는다."""
        project, manager, request_type = await _setup(session, permissions)
        service = EmailChannelService(session, permissions, settings)
        body = {
            "project_id": project.id,
            "address": "help@ours.example",
            "outbound_from": "help@ours.example",
            "inbound": dict(GOOD_INBOUND),
            "password": PASSWORD,
            "default_request_type_id": request_type.id,
        }
        await service.create(actor_for(manager), **body)  # type: ignore[arg-type]
        with pytest.raises(ConflictError) as exc:
            await service.create(actor_for(manager), **body)  # type: ignore[arg-type]
        assert exc.value.code == "desk.email_address_taken"


class TestSavingAChannel:
    async def test_a_good_channel_saves(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        project, manager, request_type = await _setup(session, permissions)
        view = await EmailChannelService(session, permissions, settings).create(
            actor_for(manager),
            project_id=project.id,
            address="Help@Ours.Example",
            outbound_from="help@ours.example",
            inbound=dict(GOOD_INBOUND),
            password=PASSWORD,
            default_request_type_id=request_type.id,  # type: ignore[attr-defined]
        )
        # 주소는 소문자로 정규화한다 — 매칭이 대소문자로 갈리면 안 된다.
        assert view.channel.address == "help@ours.example"
        assert view.has_password is True
        assert view.request_type_name == "Broken thing"

    async def test_the_password_is_not_in_the_config(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**이 시험이 이 파일의 이유 중 하나다.** 설정 사전에 비밀번호가
        들어가면 그 사전이 API 응답과 로그에 그대로 실린다."""
        project, manager, request_type = await _setup(session, permissions)
        view = await EmailChannelService(session, permissions, settings).create(
            actor_for(manager),
            project_id=project.id,
            address="help@ours.example",
            outbound_from="help@ours.example",
            inbound=dict(GOOD_INBOUND),
            password=PASSWORD,
            default_request_type_id=request_type.id,  # type: ignore[attr-defined]
        )
        assert PASSWORD not in str(view.channel.inbound)
        assert "password" not in view.channel.inbound
        # 저장된 것은 암호문이다.
        assert view.channel.inbound_password_enc is not None
        assert PASSWORD not in view.channel.inbound_password_enc

    async def test_the_stored_password_can_be_read_back_by_the_worker(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """암호화만 하고 못 풀면 폴링이 영원히 실패한다."""
        from ieum.core.crypto import SecretBox

        project, manager, request_type = await _setup(session, permissions)
        view = await EmailChannelService(session, permissions, settings).create(
            actor_for(manager),
            project_id=project.id,
            address="help@ours.example",
            outbound_from="help@ours.example",
            inbound=dict(GOOD_INBOUND),
            password=PASSWORD,
            default_request_type_id=request_type.id,  # type: ignore[attr-defined]
        )
        box = SecretBox(settings.secret_key.get_secret_value(), purpose="desk.email")
        assert view.channel.inbound_password_enc is not None
        assert box.decrypt(view.channel.inbound_password_enc) == PASSWORD

    @pytest.mark.parametrize(
        "inbound",
        [
            {"user": "help"},  # 호스트가 없다
            {"host": "imap.example"},  # 사용자가 없다
            {"host": "imap.example", "user": "help", "port": "구백구십삼"},
            {"host": "imap.example", "user": "help", "password": "여기 넣지 않는다"},
        ],
    )
    async def test_a_config_that_cannot_connect_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        settings: Settings,
        inbound: dict[str, object],
    ) -> None:
        """저장되고 폴링만 실패하면 관리자는 저장이 성공했으니 됐다고 믿는다."""
        project, manager, request_type = await _setup(session, permissions)
        with pytest.raises(ValidationError) as exc:
            await EmailChannelService(session, permissions, settings).create(
                actor_for(manager),
                project_id=project.id,
                address="help@ours.example",
                outbound_from="help@ours.example",
                inbound=inbound,
                password=PASSWORD,
                default_request_type_id=request_type.id,  # type: ignore[attr-defined]
            )
        assert exc.value.code == "desk.email_inbound_invalid"

    async def test_a_channel_without_a_password_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        project, manager, request_type = await _setup(session, permissions)
        with pytest.raises(ValidationError) as exc:
            await EmailChannelService(session, permissions, settings).create(
                actor_for(manager),
                project_id=project.id,
                address="help@ours.example",
                outbound_from="help@ours.example",
                inbound=dict(GOOD_INBOUND),
                password=None,
                default_request_type_id=request_type.id,  # type: ignore[attr-defined]
            )
        assert exc.value.code == "desk.email_password_required"

    @pytest.mark.parametrize("address", ["not-an-email", "a@b", "", "a b@c.example"])
    async def test_a_bad_address_is_refused(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        settings: Settings,
        address: str,
    ) -> None:
        project, manager, request_type = await _setup(session, permissions)
        with pytest.raises(ValidationError) as exc:
            await EmailChannelService(session, permissions, settings).create(
                actor_for(manager),
                project_id=project.id,
                address=address,
                outbound_from="help@ours.example",
                inbound=dict(GOOD_INBOUND),
                password=PASSWORD,
                default_request_type_id=request_type.id,  # type: ignore[attr-defined]
            )
        assert exc.value.code == "desk.invalid_email"

    async def test_a_duplicate_address_is_refused_with_a_reason(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """DB 의 unique 로만 두면 저장이 500 이 되고, 관리자는 무엇이 막았는지
        못 듣는다."""
        project, manager, request_type = await _setup(session, permissions)
        service = EmailChannelService(session, permissions, settings)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            address="help@ours.example",
            outbound_from="help@ours.example",
            inbound=dict(GOOD_INBOUND),
            password=PASSWORD,
            default_request_type_id=request_type.id,  # type: ignore[attr-defined]
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                address="HELP@ours.example",
                outbound_from="help@ours.example",
                inbound=dict(GOOD_INBOUND),
                password=PASSWORD,
                default_request_type_id=request_type.id,  # type: ignore[attr-defined]
            )
        assert exc.value.code == "desk.email_address_taken"

    async def test_a_request_type_from_another_project_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """그 티켓은 이 프로젝트의 큐에 안 걸린다 — 메일은 들어왔는데 아무도
        못 본다."""
        project, manager, _ = await _setup(session, permissions)
        _, _, theirs = await _setup(session, permissions)
        await grant(
            session,
            principal_id=manager.id,
            permissions_granted=(desk_perms.EMAIL_MANAGE,),
            scope=Scope.project(project.id),
        )
        with pytest.raises(ValidationError) as exc:
            await EmailChannelService(session, permissions, settings).create(
                actor_for(manager),
                project_id=project.id,
                address="help@ours.example",
                outbound_from="help@ours.example",
                inbound=dict(GOOD_INBOUND),
                password=PASSWORD,
                default_request_type_id=theirs.id,  # type: ignore[attr-defined]
            )
        assert exc.value.code == "desk.unknown_request_type"


class TestChangingAChannel:
    async def _channel(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> tuple[User, object]:
        project, manager, request_type = await _setup(session, permissions)
        view = await EmailChannelService(session, permissions, settings).create(
            actor_for(manager),
            project_id=project.id,
            address=f"help-{new_id().hex[-6:]}@ours.example",
            outbound_from="help@ours.example",
            inbound=dict(GOOD_INBOUND),
            password=PASSWORD,
            default_request_type_id=request_type.id,  # type: ignore[attr-defined]
        )
        return manager, view.channel

    async def test_an_omitted_password_is_kept(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**폼은 비밀번호 칸을 비워 두고 저장한다.** 빈 값을 지우기로 읽으면
        이름만 고쳐도 메일 수신이 멈춘다."""
        manager, channel = await self._channel(session, permissions, settings)
        before = channel.inbound_password_enc  # type: ignore[attr-defined]
        view = await EmailChannelService(session, permissions, settings).update(
            actor_for(manager),
            channel.id,  # type: ignore[attr-defined]
            outbound_from="other@ours.example",
        )
        assert view.channel.inbound_password_enc == before
        assert view.has_password is True

    async def test_a_new_password_replaces_the_old_one(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        manager, channel = await self._channel(session, permissions, settings)
        before = channel.inbound_password_enc  # type: ignore[attr-defined]
        view = await EmailChannelService(session, permissions, settings).update(
            actor_for(manager),
            channel.id,  # type: ignore[attr-defined]
            password="a-different-secret-5678",
        )
        assert view.channel.inbound_password_enc != before

    async def test_changing_only_the_config_keeps_working(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """설정 검증이 비밀번호를 요구하는데, 이미 저장된 것이 있으면 성립한다.
        안 그러면 폴더 이름 하나를 고치려고 비밀번호를 다시 적어야 한다."""
        manager, channel = await self._channel(session, permissions, settings)
        view = await EmailChannelService(session, permissions, settings).update(
            actor_for(manager),
            channel.id,  # type: ignore[attr-defined]
            inbound={**GOOD_INBOUND, "folder": "Support"},
        )
        assert view.channel.inbound["folder"] == "Support"
        assert view.has_password is True

    async def test_re_enabling_clears_the_last_error(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """남겨 두면 관리자는 아직 망가진 줄 알고 그 메시지를 다시는 믿지
        않게 된다."""
        manager, channel = await self._channel(session, permissions, settings)
        channel.last_error = "ImapError: 로그인할 수 없다"  # type: ignore[attr-defined]
        channel.is_enabled = False  # type: ignore[attr-defined]
        await session.flush()

        view = await EmailChannelService(session, permissions, settings).update(
            actor_for(manager),
            channel.id,  # type: ignore[attr-defined]
            is_enabled=True,
        )
        assert view.channel.last_error is None

    async def test_deleting_archives_it(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """오간 메일의 기록은 그대로 둔다 — 지난 티켓의 스레드가 채널을
        지우는 것으로 끊기면 안 된다."""
        manager, channel = await self._channel(session, permissions, settings)
        service = EmailChannelService(session, permissions, settings)
        await service.delete(actor_for(manager), channel.id)  # type: ignore[attr-defined]
        assert channel.archived_at is not None  # type: ignore[attr-defined]
        with pytest.raises(NotFoundError):
            await service.delete(actor_for(manager), channel.id)  # type: ignore[attr-defined]


class TestPermissions:
    async def test_a_stranger_cannot_list(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        project, _, _ = await _setup(session, permissions)
        nobody = User(email=f"n-{new_id()}@example.com", display_name="아무나", status="active")
        session.add(nobody)
        await session.flush()
        with pytest.raises(PermissionDeniedError):
            await EmailChannelService(session, permissions, settings).list_for(
                actor_for(nobody), project.id
            )

    async def test_it_needs_step_up(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**이 손잡이는 메일함 비밀번호를 받는다.** 그리고 받는 주소를 바꾸면
        그 뒤로 오는 고객의 메일이 다른 프로젝트의 티켓이 된다."""
        project, manager, _ = await _setup(session, permissions)
        # `actor_for` 는 **MFA 를 통과한** 액터를 준다(step-up 권한도 쓰므로).
        # 여기서는 그 반대가 필요해서 직접 만든다.
        plain = Actor(user_id=manager.id, email=manager.email, is_active=True)
        with pytest.raises(StepUpRequiredError):
            await EmailChannelService(session, permissions, settings).list_for(plain, project.id)
