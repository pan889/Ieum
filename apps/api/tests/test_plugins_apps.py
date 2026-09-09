"""앱 등록과 자리, 그리고 앱이 써 둔 글 (M6 "플러그인 훅"). 실제 Postgres 를 쓴다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **자리는 관리자의 것, 안은 앱의 것이다.** 앱은 자기 자리를 만들거나 옮길
  수 없다. 관리자가 준 자리에만 글을 쓴다. 이 경계가 무너지면 앱 하나가
  전이 버튼 옆으로 옮겨 앉아 우리가 쓴 글처럼 보인다.
- **끈 앱은 아무것도 못 한다.** 자리도 안 보이고, 글도 못 쓰고, 이벤트도
  안 받는다. 셋 중 하나라도 살아 있으면 끈 것이 아니다.
- **지운 앱은 흔적을 남기지 않는다.** 자리·글·웹훅이 함께 사라진다. 웹훅이
  남으면 지운 앱의 주소로 이벤트가 계속 나간다.
- **남의 앱 자리에는 못 쓴다.** 토큰 하나가 다른 앱의 칸을 채울 수 있으면
  등록이라는 것이 뜻을 잃는다.
- **앱 등록 권한이 웹훅 권한의 우회로가 아니다.** 앱을 등록하며 이벤트
  주소를 붙이는 것은 웹훅을 만드는 일이고, 그 문은 그쪽 권한이 지킨다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.notify import permissions as notify_perms
from ieum.modules.notify.models import Webhook
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository
from ieum.modules.plugins import permissions as plugin_perms
from ieum.modules.plugins.models import App, AppPanel, AppSlot
from ieum.modules.plugins.service import (
    MAX_SLOTS_PER_APP,
    AppService,
    AppTokenService,
    NewSlot,
)

pytestmark = pytest.mark.integration

#: 앱을 등록하려면 웹훅 권한도 필요하다 — 이벤트 주소를 붙이는 일이 곧
#: 웹훅을 만드는 일이라서 그렇다. 그 규칙 자체를 붙잡는 시험이 아래에 있다.
FULL = (*plugin_perms.ALL, *notify_perms.ALL)


@pytest.fixture(autouse=True)
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    set_permission_service(service)
    return service


async def _user(session: AsyncSession) -> User:
    row = User(
        email=f"u-{new_id()}@example.com",
        display_name=f"사람 {new_id().hex[-6:]}",
        status="active",
    )
    session.add(row)
    await session.flush()
    return row


async def _actor(
    session: AsyncSession,
    *,
    global_grants: Sequence[str] = (),
    project: Project | None = None,
    project_grants: Sequence[str] = (),
) -> Actor:
    user = await _user(session)
    repo = RoleRepository(session)
    if global_grants:
        role = Role(name=f"g-{new_id().hex[-8:]}", scope_kind="global")
        repo.add(role)
        await session.flush()
        for permission in global_grants:
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id, scope=Scope.global_(), principal_kind="user", principal_id=user.id
        )
    if project is not None and project_grants:
        role = Role(name=f"p-{new_id().hex[-8:]}", scope_kind="project")
        repo.add(role)
        await session.flush()
        for permission in project_grants:
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id,
            scope=Scope.project(project.id),
            principal_kind="user",
            principal_id=user.id,
        )
    await session.flush()
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_verified=True,
        mfa_satisfied_at=utcnow(),
    )


async def _issue(session: AsyncSession) -> tuple[Project, Issue]:
    project = Project(key=f"P{new_id().hex[-5:].upper()}", name="플러그인")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(state)
    kind = IssueType(project_id=project.id, name="Task", workflow_id=workflow.id)
    session.add(kind)
    await session.flush()
    issue = Issue(
        project_id=project.id,
        type_id=kind.id,
        state_id=state.id,
        key_seq=1,
        summary="빌드가 깨졌다",
    )
    session.add(issue)
    await session.flush()
    return project, issue


async def _app(
    session: AsyncSession,
    permissions: PermissionService,
    settings: Settings,
    actor: Actor,
    *,
    slug: str | None = None,
) -> tuple[App, str]:
    return await AppService(session, permissions, settings).register(
        actor, name="CI 봇", slug=slug or f"ci-{new_id().hex[-6:]}"
    )


class TestRegistering:
    async def test_the_token_comes_back_once_and_is_not_stored(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**원문을 우리가 들고 있지 않다.** 목록을 읽는 사람이 모든 앱을
        사칭할 수 있으면 등록이 뜻을 잃는다 (PAT 과 같은 규칙)."""
        actor = await _actor(session, global_grants=FULL)
        row, token = await _app(session, permissions, settings, actor)
        assert token.startswith(AppTokenService.PREFIX)
        assert token not in (row.token_hash, row.token_prefix)
        assert row.token_prefix in token

    async def test_the_same_slug_twice_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        await _app(session, permissions, settings, actor, slug="ci-bot")
        with pytest.raises(ConflictError) as caught:
            await _app(session, permissions, settings, actor, slug="ci-bot")
        assert caught.value.code == "plugins.slug_taken"

    async def test_a_nameless_app_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        with pytest.raises(ValidationError) as caught:
            await AppService(session, permissions, settings).register(
                actor, name="   ", slug="ci-bot"
            )
        assert caught.value.code == "plugins.name_required"

    async def test_a_stranger_cannot_register(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        weak = await _actor(session, global_grants=[plugin_perms.APP_VIEW])
        with pytest.raises(PermissionDeniedError):
            await _app(session, permissions, settings, weak)

    async def test_rotating_kills_the_old_token(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        row, first = await _app(session, permissions, settings, actor)
        second = await AppService(session, permissions, settings).rotate_token(actor, row.id)
        assert first != second
        assert await AppTokenService(session).authenticate(second)
        with pytest.raises(AuthenticationError):
            await AppTokenService(session).authenticate(first)


class TestEventStream:
    async def test_registering_with_events_opens_a_webhook(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """서버 이벤트를 여기서 배달하지 않는다 — notify 의 웹훅이 서명·재시도·
        전송 로그를 이미 들고 있다."""
        actor = await _actor(session, global_grants=FULL)
        row, _ = await AppService(session, permissions, settings).register(
            actor,
            name="CI 봇",
            slug="ci-hooked",
            events=["issue.created"],
            event_url="https://ci.example.com/hook",
        )
        assert row.webhook_id is not None
        hook = await session.get(Webhook, row.webhook_id)
        assert hook is not None
        assert hook.events == ["issue.created"]
        # 웹훅 시크릿은 앱 토큰과 **다른 값**이다. 하나가 새면 나머지 하나로
        # 다른 일을 할 수 없어야 한다.
        assert hook.secret_enc

    async def test_app_manage_alone_cannot_open_a_webhook(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**이 시험이 contracts 가 서비스를 그대로 부르는 이유다.**

        앱 등록 권한만으로 웹훅을 만들 수 있으면 그게 곧 우회로다 — 웹훅
        권한을 뺏어 둔 사람이 앱을 하나 등록해 이벤트를 받아 갈 수 있다.
        """
        actor = await _actor(session, global_grants=list(plugin_perms.ALL))
        with pytest.raises(PermissionDeniedError):
            await AppService(session, permissions, settings).register(
                actor,
                name="몰래",
                slug="sneaky",
                events=["issue.created"],
                event_url="https://evil.example.com/hook",
            )

    async def test_events_without_an_address_are_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        with pytest.raises(ValidationError) as caught:
            await AppService(session, permissions, settings).register(
                actor, name="CI", slug="ci-noaddr", events=["issue.created"]
            )
        assert caught.value.code == "plugins.event_url_required"

    async def test_disabling_the_app_stops_the_events(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**끄면 이벤트도 멈춘다.** 자리만 감추고 이벤트는 계속 보내면 끈
        앱이 이슈 제목을 계속 받아 가는 상태가 조용히 남는다."""
        actor = await _actor(session, global_grants=FULL)
        service = AppService(session, permissions, settings)
        row, _ = await service.register(
            actor,
            name="CI",
            slug="ci-off",
            events=["issue.created"],
            event_url="https://ci.example.com/hook",
        )
        await service.set_enabled(actor, row.id, enabled=False)
        hook = await session.get(Webhook, row.webhook_id)
        assert hook is not None
        assert hook.enabled is False


class TestPlacing:
    async def test_a_link_placement_keeps_its_template(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        row, _ = await _app(session, permissions, settings, actor)
        placed = await AppService(session, permissions, settings).place(
            actor,
            row.id,
            NewSlot(
                slot="issue.link",
                kind="link",
                label="빌드 보기",
                url_template="https://ci.example.com/b/{issue_key}",
            ),
        )
        assert placed.url_template == "https://ci.example.com/b/{issue_key}"

    async def test_a_dangerous_link_never_reaches_the_table(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """검사는 slots.py 에 있고 시험도 거기 있다. 여기서 보는 것은
        **그 검사가 이 경로에 실제로 걸려 있는가** 다."""
        actor = await _actor(session, global_grants=FULL)
        row, _ = await _app(session, permissions, settings, actor)
        with pytest.raises(ValidationError) as caught:
            await AppService(session, permissions, settings).place(
                actor,
                row.id,
                NewSlot(
                    slot="issue.link",
                    kind="link",
                    label="누르면",
                    url_template="javascript:alert(1)",
                ),
            )
        assert caught.value.code == "plugins.url_scheme"
        assert list((await session.execute(select(AppSlot.id))).scalars().all()) == []

    async def test_the_same_label_twice_in_one_slot_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """같은 글자 두 개가 나란히 있으면 어느 쪽을 눌러야 하는지 알 수 없다."""
        actor = await _actor(session, global_grants=FULL)
        row, _ = await _app(session, permissions, settings, actor)
        service = AppService(session, permissions, settings)
        payload = NewSlot(
            slot="issue.link",
            kind="link",
            label="빌드",
            url_template="https://ci.example.com/b",
        )
        await service.place(actor, row.id, payload)
        with pytest.raises(ConflictError) as caught:
            await service.place(actor, row.id, payload)
        assert caught.value.code == "plugins.slot_label_taken"

    async def test_a_panel_cannot_carry_a_url(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        row, _ = await _app(session, permissions, settings, actor)
        with pytest.raises(ValidationError) as caught:
            await AppService(session, permissions, settings).place(
                actor,
                row.id,
                NewSlot(
                    slot="issue.panel",
                    kind="panel",
                    label="빌드 상태",
                    url_template="https://ci.example.com/b",
                ),
            )
        assert caught.value.code == "plugins.panel_has_url"

    async def test_there_is_a_ceiling(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """상한이 없으면 앱 하나가 이슈 화면을 자기 링크로 덮을 수 있다."""
        actor = await _actor(session, global_grants=FULL)
        row, _ = await _app(session, permissions, settings, actor)
        service = AppService(session, permissions, settings)
        for i in range(MAX_SLOTS_PER_APP):
            await service.place(
                actor,
                row.id,
                NewSlot(
                    slot="issue.link",
                    kind="link",
                    label=f"빌드 {i}",
                    url_template="https://ci.example.com/b",
                ),
            )
        with pytest.raises(ConflictError) as caught:
            await service.place(
                actor,
                row.id,
                NewSlot(
                    slot="issue.link",
                    kind="link",
                    label="하나 더",
                    url_template="https://ci.example.com/b",
                ),
            )
        assert caught.value.code == "plugins.too_many_slots"

    async def test_another_apps_placement_is_not_found(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """남의 앱의 자리는 존재 자체를 숨긴다 — 다른 앱의 id 로 물으면
        "권한이 없다" 가 아니라 "없다" 다."""
        actor = await _actor(session, global_grants=FULL)
        service = AppService(session, permissions, settings)
        mine, _ = await _app(session, permissions, settings, actor, slug="mine")
        theirs, _ = await _app(session, permissions, settings, actor, slug="theirs")
        placed = await service.place(
            actor,
            theirs.id,
            NewSlot(
                slot="issue.link",
                kind="link",
                label="남의 것",
                url_template="https://x.example.com/a",
            ),
        )
        with pytest.raises(NotFoundError):
            await service.unplace(actor, mine.id, placed.id)


class TestWhatTheAppWrites:
    async def test_it_writes_by_issue_key(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """앱이 아는 것은 커밋 제목에 실려 온 `PROJ-123` 이고 우리 UUID 가
        아니다 (A22 와 같은 판단)."""
        actor = await _actor(session, global_grants=FULL)
        project, issue = await _issue(session)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).place(
            actor,
            row.id,
            NewSlot(slot="issue.panel", kind="panel", label="빌드 상태"),
        )
        app = await AppTokenService(session).authenticate(token)
        await AppTokenService(session).write_panel(
            app, issue_key=f"{project.key}-1", label="빌드 상태", body="빌드 **통과**"
        )
        found = (await session.execute(select(AppPanel))).scalars().all()
        assert [panel.body for panel in found] == ["빌드 **통과**"]
        assert [panel.issue_id for panel in found] == [issue.id]

    async def test_writing_twice_overwrites(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """이 칸은 지금 상태를 말하는 자리고 이력이 아니다."""
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드")
        )
        app = await AppTokenService(session).authenticate(token)
        service = AppTokenService(session)
        await service.write_panel(app, issue_key=f"{project.key}-1", label="빌드", body="돌고 있다")
        await service.write_panel(app, issue_key=f"{project.key}-1", label="빌드", body="통과")
        found = (await session.execute(select(AppPanel))).scalars().all()
        assert [panel.body for panel in found] == ["통과"]

    async def test_an_app_cannot_write_where_it_was_not_placed(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**앱이 자리를 만들지 못한다.** 관리자가 안 줬으면 쓸 곳이 없다 —
        이 경계가 무너지면 앱이 스스로 화면 자리를 늘린다."""
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        _, token = await _app(session, permissions, settings, actor)
        app = await AppTokenService(session).authenticate(token)
        with pytest.raises(PermissionDeniedError) as caught:
            await AppTokenService(session).write_panel(
                app, issue_key=f"{project.key}-1", label="안 받은 자리", body="글"
            )
        assert caught.value.code == "plugins.no_such_placement"

    async def test_one_app_cannot_write_into_anothers_panel(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """토큰 하나가 다른 앱의 칸을 채울 수 있으면 등록이 뜻을 잃는다."""
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        service = AppService(session, permissions, settings)
        first, _ = await _app(session, permissions, settings, actor, slug="first")
        _, second_token = await _app(session, permissions, settings, actor, slug="second")
        await service.place(
            actor, first.id, NewSlot(slot="issue.panel", kind="panel", label="빌드")
        )
        intruder = await AppTokenService(session).authenticate(second_token)
        with pytest.raises(PermissionDeniedError) as caught:
            await AppTokenService(session).write_panel(
                intruder, issue_key=f"{project.key}-1", label="빌드", body="내가 쓴다"
            )
        assert caught.value.code == "plugins.no_such_placement"

    async def test_a_disabled_app_cannot_authenticate(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """끄고도 쓸 수 있으면 끈 것이 아니다."""
        actor = await _actor(session, global_grants=FULL)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).set_enabled(actor, row.id, enabled=False)
        with pytest.raises(AuthenticationError) as caught:
            await AppTokenService(session).authenticate(token)
        assert caught.value.code == "plugins.app_disabled"

    async def test_a_missing_issue_is_not_found(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드")
        )
        app = await AppTokenService(session).authenticate(token)
        with pytest.raises(NotFoundError):
            await AppTokenService(session).write_panel(
                app, issue_key="NOPE-9999", label="빌드", body="글"
            )

    async def test_clearing_removes_the_row(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """빈 글을 쓰는 것과 거두는 것은 다르다 — 빈 칸을 그리면 사람은
        그것을 고장으로 읽는다."""
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드")
        )
        app = await AppTokenService(session).authenticate(token)
        service = AppTokenService(session)
        await service.write_panel(app, issue_key=f"{project.key}-1", label="빌드", body="통과")
        assert await service.clear_panel(app, issue_key=f"{project.key}-1", label="빌드") is True
        assert (await session.execute(select(AppPanel))).scalars().all() == []
        # 두 번 거두면 아무 일도 안 한다. 없는 것을 지우는 것은 오류가 아니다.
        assert await service.clear_panel(app, issue_key=f"{project.key}-1", label="빌드") is False

    async def test_an_empty_body_is_refused(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        row, token = await _app(session, permissions, settings, actor)
        await AppService(session, permissions, settings).place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드")
        )
        app = await AppTokenService(session).authenticate(token)
        with pytest.raises(ValidationError) as caught:
            await AppTokenService(session).write_panel(
                app, issue_key=f"{project.key}-1", label="빌드", body="   "
            )
        assert caught.value.code == "plugins.body_required"


class TestWhatTheScreenReads:
    async def test_the_issue_screen_gets_filled_links_and_bodies(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """자리표가 실제 값으로 채워져 나간다. 안 채우면 사람이 중괄호를 본다."""
        project, issue = await _issue(session)
        actor = await _actor(
            session, global_grants=FULL, project=project, project_grants=["issue.view"]
        )
        service = AppService(session, permissions, settings)
        row, token = await _app(session, permissions, settings, actor)
        await service.place(
            actor,
            row.id,
            NewSlot(
                slot="issue.link",
                kind="link",
                label="빌드 보기",
                url_template="https://ci.example.com/{project_key}/{issue_key}",
            ),
        )
        await service.place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드 상태")
        )
        app = await AppTokenService(session).authenticate(token)
        await AppTokenService(session).write_panel(
            app, issue_key=f"{project.key}-1", label="빌드 상태", body="통과"
        )

        found = await service.contributions_for_issue(actor, issue.id)
        links = [row for row in found if row.kind == "link"]
        panels = [row for row in found if row.kind == "panel"]
        assert links[0].url == f"https://ci.example.com/{project.key}/{project.key}-1"
        assert panels[0].body == "통과"

    async def test_an_unwritten_panel_takes_no_space(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """빈 칸을 그리면 사람은 그것을 고장으로 읽는다."""
        project, issue = await _issue(session)
        actor = await _actor(
            session, global_grants=FULL, project=project, project_grants=["issue.view"]
        )
        service = AppService(session, permissions, settings)
        row, _ = await _app(session, permissions, settings, actor)
        await service.place(
            actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드 상태")
        )
        assert await service.contributions_for_issue(actor, issue.id) == []

    async def test_a_disabled_app_shows_nothing(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        project, issue = await _issue(session)
        actor = await _actor(
            session, global_grants=FULL, project=project, project_grants=["issue.view"]
        )
        service = AppService(session, permissions, settings)
        row, _ = await _app(session, permissions, settings, actor)
        await service.place(
            actor,
            row.id,
            NewSlot(
                slot="issue.link",
                kind="link",
                label="빌드",
                url_template="https://ci.example.com/b",
            ),
        )
        assert len(await service.contributions_for_issue(actor, issue.id)) == 1
        await service.set_enabled(actor, row.id, enabled=False)
        assert await service.contributions_for_issue(actor, issue.id) == []

    async def test_you_must_be_able_to_see_the_issue(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """패널 본문은 앱이 그 이슈에 대해 쓴 글이고, 그것은 이슈 내용의
        일부다. 전역 앱 권한만으로 읽히면 이슈 권한이 뚫린다."""
        _, issue = await _issue(session)
        outsider = await _actor(session, global_grants=FULL)
        with pytest.raises(PermissionDeniedError):
            await AppService(session, permissions, settings).contributions_for_issue(
                outsider, issue.id
            )

    async def test_settings_links_do_not_need_an_issue(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        service = AppService(session, permissions, settings)
        row, _ = await _app(session, permissions, settings, actor)
        await service.place(
            actor,
            row.id,
            NewSlot(
                slot="settings.link",
                kind="link",
                label="CI 설정",
                url_template="https://ci.example.com/settings",
            ),
        )
        found = await service.links_for(actor, "settings.link")
        assert [row.label for row in found] == ["CI 설정"]


class TestRemoving:
    async def test_removing_takes_the_slots_and_the_panels(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """자리를 남기면 화면에 주인 없는 칸이 남는다."""
        actor = await _actor(session, global_grants=FULL)
        project, _ = await _issue(session)
        service = AppService(session, permissions, settings)
        row, token = await _app(session, permissions, settings, actor)
        await service.place(actor, row.id, NewSlot(slot="issue.panel", kind="panel", label="빌드"))
        app = await AppTokenService(session).authenticate(token)
        await AppTokenService(session).write_panel(
            app, issue_key=f"{project.key}-1", label="빌드", body="통과"
        )
        await service.remove(actor, row.id)
        assert (await session.execute(select(AppSlot))).scalars().all() == []
        assert (await session.execute(select(AppPanel))).scalars().all() == []

    async def test_removing_takes_the_webhook(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        """**웹훅이 남으면 지운 앱의 주소로 이벤트가 계속 나간다.**
        지웠다고 믿는 쪽이 틀리게 된다."""
        actor = await _actor(session, global_grants=FULL)
        service = AppService(session, permissions, settings)
        row, _ = await service.register(
            actor,
            name="CI",
            slug="ci-gone",
            events=["issue.created"],
            event_url="https://ci.example.com/hook",
        )
        webhook_id = row.webhook_id
        assert webhook_id is not None
        await service.remove(actor, row.id)
        assert await session.get(Webhook, webhook_id) is None

    async def test_a_missing_app_is_not_found(
        self, session: AsyncSession, permissions: PermissionService, settings: Settings
    ) -> None:
        actor = await _actor(session, global_grants=FULL)
        with pytest.raises(NotFoundError):
            await AppService(session, permissions, settings).remove(actor, new_id())
