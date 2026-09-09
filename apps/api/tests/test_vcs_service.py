"""저장소 등록과 링크 기록 (feature-map A22). 실제 Postgres 를 쓴다.

키를 읽는 일은 `test_vcs_refs.py` 가 값으로 붙잡고, 몸을 읽는 일은
`test_vcs_webhooks.py` 가 붙잡는다. **여기서 보는 것은 경계다** — 바깥에서
온 몸이 어디까지 닿을 수 있는가. 틀리면 조용히 틀린다:

- **저장소에 적힌 프로젝트 밖은 못 건드린다.** 시크릿 하나로 설치 전체의
  이슈에 글을 붙일 수 있으면 그건 연동이 아니라 구멍이다.
- **등록은 적은 프로젝트 전부에 권한이 있어야 한다.** 하나만 보면 권한 있는
  프로젝트 하나로 남의 프로젝트에 붙일 길이 열린다.
- **다시 와도 한 줄이다.** 웹훅은 재전송된다.
- **닫는다는 표시는 켜지기만 한다.** 두 번째 몸에 그 낱말이 없다고 꺼 버리면
  사람이 한 말을 우리가 지우는 것이다.
- **못 찾은 키는 오류가 아니다.** 4xx 를 주면 코드 호스트가 재전송을
  반복하고 사람이 웹훅을 끈다.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.issues.workflow import DEFAULT_WORKFLOW_STATES
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository
from ieum.modules.vcs import permissions as perms
from ieum.modules.vcs.models import ChangeLink, Repository
from ieum.modules.vcs.service import (
    MAX_PROJECTS,
    NewRepository,
    RepositoryService,
    record,
    secret_box,
)
from ieum.modules.vcs.webhooks import Change


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    return service


@pytest_asyncio.fixture
async def state(session: AsyncSession) -> WorkflowState:
    """이슈를 만들려면 워크플로우 상태가 하나 있어야 한다."""
    wf = Workflow(name=f"WF-{secrets.token_hex(4)}", is_builtin=False)
    session.add(wf)
    await session.flush()
    rows = [
        WorkflowState(
            workflow_id=wf.id,
            name=name,
            category=category,
            position=position,
            is_initial=is_initial,
        )
        for position, (name, category, is_initial) in enumerate(DEFAULT_WORKFLOW_STATES)
    ]
    session.add_all(rows)
    await session.flush()
    session.add(IssueType(project_id=None, name=f"Task-{secrets.token_hex(3)}", workflow_id=wf.id))
    await session.flush()
    return rows[0]


@pytest_asyncio.fixture
async def issue_type(session: AsyncSession, state: WorkflowState) -> IssueType:
    found = (
        await session.execute(select(IssueType).where(IssueType.workflow_id == state.workflow_id))
    ).scalar_one()
    return found


async def _project(session: AsyncSession, key: str | None = None) -> Project:
    row = Project(key=key or f"V{secrets.token_hex(3).upper()}", name="VCS Project")
    session.add(row)
    await session.flush()
    return row


async def _actor(
    session: AsyncSession, projects: Sequence[Project], grants: Sequence[str]
) -> Actor:
    """주어진 프로젝트들에서 주어진 권한을 가진 사람."""
    user = User(email=f"u-{new_id()}@example.com", display_name="팀원", status="active")
    session.add(user)
    await session.flush()
    repo = RoleRepository(session)
    role = Role(name=f"r-{secrets.token_hex(4)}", scope_kind="project")
    repo.add(role)
    await session.flush()
    for permission in grants:
        repo.grant(role.id, permission)
    for project in projects:
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
        # 등록은 step-up 대상이다. 실제로 MFA 를 통과한 사람이어야 한다 —
        # 시각만 채우면 2FA 없는 계정이 민감 작업을 전부 통과한다.
        mfa_verified=True,
        mfa_satisfied_at=utcnow(),
    )


async def _issue(
    session: AsyncSession, project: Project, issue_type: IssueType, state: WorkflowState, seq: int
) -> Issue:
    row = Issue(
        project_id=project.id,
        key_seq=seq,
        type_id=issue_type.id,
        state_id=state.id,
        summary=f"이슈 {seq}",
    )
    session.add(row)
    await session.flush()
    return row


async def _repository(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    projects: Sequence[Project],
    *,
    name: str | None = None,
    provider: str = "github",
) -> Repository:
    issued = await RepositoryService(session, permissions).create(
        actor,
        NewRepository(
            provider=provider,
            name=name or f"acme/{secrets.token_hex(4)}",
            project_ids=tuple(project.id for project in projects),
        ),
    )
    return issued.repository


def _commit(
    text: str,
    *,
    ref: str | None = None,
    title: str | None = None,
    when: datetime | None = None,
) -> Change:
    return Change(
        kind="commit",
        ref=ref or secrets.token_hex(20),
        title=title if title is not None else text.splitlines()[0],
        url="https://github.com/acme/web/commit/x",
        author="sujin",
        happened_at=when or utcnow(),
        text=text,
    )


async def _links(session: AsyncSession, issue: Issue) -> list[ChangeLink]:
    rows = await session.execute(select(ChangeLink).where(ChangeLink.issue_id == issue.id))
    return list(rows.scalars().all())


class TestRegistering:
    async def test_the_secret_is_shown_once_and_stored_encrypted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """읽을 수 있게 두면 그 화면을 볼 수 있는 사람 전부가 바깥에서
        이슈에 글을 붙일 수 있다."""
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        issued = await RepositoryService(session, permissions).create(
            actor,
            NewRepository(provider="github", name="acme/web", project_ids=(project.id,)),
        )
        assert issued.secret
        assert issued.repository.secret_enc != issued.secret
        assert secret_box().decrypt(issued.repository.secret_enc) == issued.secret

    async def test_every_listed_project_needs_the_permission(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**하나만 보면 안 된다.** 권한 있는 프로젝트 하나를 끼워 넣어
        남의 프로젝트에 커밋을 붙일 길이 열린다."""
        mine = await _project(session)
        theirs = await _project(session)
        actor = await _actor(session, [mine], perms.ALL)
        with pytest.raises(PermissionDeniedError):
            await RepositoryService(session, permissions).create(
                actor,
                NewRepository(
                    provider="github", name="acme/mono", project_ids=(mine.id, theirs.id)
                ),
            )

    async def test_a_monorepo_spanning_projects_i_manage_is_fine(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        one = await _project(session)
        two = await _project(session)
        actor = await _actor(session, [one, two], perms.ALL)
        issued = await RepositoryService(session, permissions).create(
            actor,
            NewRepository(provider="github", name="acme/mono", project_ids=(one.id, two.id)),
        )
        assert set(issued.repository.project_ids) == {str(one.id), str(two.id)}

    async def test_the_same_repository_cannot_be_registered_twice(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """두 번 등록되면 커밋 하나가 두 줄로 붙는다."""
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        await _repository(session, permissions, actor, [project], name="acme/dup")
        with pytest.raises(ConflictError):
            await _repository(session, permissions, actor, [project], name="acme/dup")

    async def test_a_repository_without_projects_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """ "비우면 전부" 로 두면 시크릿이 새는 날 그 하나로 설치 전체에
        닿는다."""
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(ValidationError) as caught:
            await RepositoryService(session, permissions).create(
                actor, NewRepository(provider="github", name="acme/web", project_ids=())
            )
        assert caught.value.code == "vcs.projects_required"

    async def test_an_unknown_host_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(ValidationError) as caught:
            await RepositoryService(session, permissions).create(
                actor,
                NewRepository(provider="bitbucket", name="acme/web", project_ids=(project.id,)),
            )
        assert caught.value.code == "vcs.unknown_provider"

    async def test_a_nameless_repository_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(ValidationError) as caught:
            await RepositoryService(session, permissions).create(
                actor, NewRepository(provider="github", name="   ", project_ids=(project.id,))
            )
        assert caught.value.code == "vcs.name_required"

    async def test_a_project_that_does_not_exist_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(NotFoundError):
            await RepositoryService(session, permissions).create(
                actor,
                NewRepository(provider="github", name="acme/web", project_ids=(uuid4(),)),
            )

    async def test_too_many_projects_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """상한을 넘겨 등록되면 웹훅 하나가 훑을 프로젝트가 무한히 늘어난다."""
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(ValidationError) as caught:
            await RepositoryService(session, permissions).create(
                actor,
                NewRepository(
                    provider="github",
                    name="acme/web",
                    project_ids=tuple(uuid4() for _ in range(MAX_PROJECTS + 1)),
                ),
            )
        assert caught.value.code == "vcs.too_many_projects"

    async def test_a_stranger_cannot_register(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        other = await _project(session)
        stranger = await _actor(session, [other], perms.ALL)
        with pytest.raises(PermissionDeniedError):
            await RepositoryService(session, permissions).create(
                stranger,
                NewRepository(provider="github", name="acme/web", project_ids=(project.id,)),
            )


class TestListing:
    async def test_only_the_repositories_of_that_project_come_back(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        mine = await _project(session)
        theirs = await _project(session)
        actor = await _actor(session, [mine, theirs], perms.ALL)
        wanted = await _repository(session, permissions, actor, [mine])
        await _repository(session, permissions, actor, [theirs])
        rows = await RepositoryService(session, permissions).list_for(actor, mine.id)
        assert [row.id for row in rows] == [wanted.id]

    async def test_a_disabled_repository_is_still_listed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**왜 안 오는지 보려면 목록에 있어야 한다.** 숨기면 사람은 등록이
        지워진 줄 알고 다시 등록한다."""
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        row = await _repository(session, permissions, actor, [project])
        service = RepositoryService(session, permissions)
        await service.set_enabled(actor, row.id, enabled=False)
        listed = await service.list_for(actor, project.id)
        assert [(item.id, item.is_enabled) for item in listed] == [(row.id, False)]

    async def test_a_stranger_sees_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        owner = await _actor(session, [project], perms.ALL)
        await _repository(session, permissions, owner, [project])
        elsewhere = await _project(session)
        stranger = await _actor(session, [elsewhere], perms.ALL)
        with pytest.raises(PermissionDeniedError):
            await RepositoryService(session, permissions).list_for(stranger, project.id)


class TestManaging:
    async def test_one_of_the_listed_projects_is_enough_to_manage(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """전부를 요구하면 모노레포를 등록한 뒤 **아무도 못 지우는** 상태가
        생긴다 — 한 프로젝트를 보관하거나 사람이 팀을 옮기면 그렇게 된다."""
        one = await _project(session)
        two = await _project(session)
        owner = await _actor(session, [one, two], perms.ALL)
        row = await _repository(session, permissions, owner, [one, two])
        half = await _actor(session, [two], perms.ALL)
        updated = await RepositoryService(session, permissions).set_enabled(
            half, row.id, enabled=False
        )
        assert updated.is_enabled is False

    async def test_a_stranger_cannot_delete(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        owner = await _actor(session, [project], perms.ALL)
        row = await _repository(session, permissions, owner, [project])
        elsewhere = await _project(session)
        stranger = await _actor(session, [elsewhere], perms.ALL)
        with pytest.raises(PermissionDeniedError):
            await RepositoryService(session, permissions).delete(stranger, row.id)

    async def test_deleting_takes_the_links_with_it(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """남은 링크는 눌러도 404 인 주소다 — 기록이 아니라 막다른 길이다."""
        project = await _project(session, key="DEL")
        owner = await _actor(session, [project], perms.ALL)
        row = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 1)
        assert await record(session, row, [_commit("DEL-1 고친다")]) == 1
        await RepositoryService(session, permissions).delete(owner, row.id)
        assert await _links(session, issue) == []

    async def test_a_missing_repository_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        actor = await _actor(session, [project], perms.ALL)
        with pytest.raises(NotFoundError):
            await RepositoryService(session, permissions).delete(actor, uuid4())


class TestRecording:
    async def test_a_commit_that_names_an_issue_gets_linked(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        project = await _project(session, key="REC")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 12)
        assert await record(session, repository, [_commit("REC-12 로그인을 고친다")]) == 1
        (link,) = await _links(session, issue)
        assert (link.kind, link.closing) == ("commit", False)
        assert link.title == "REC-12 로그인을 고친다"

    async def test_a_closing_word_is_recorded_but_the_state_does_not_move(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """커밋은 되돌려진다. 상태를 자동으로 옮기면 되돌릴 때 되돌아오지
        않고, 그러면 "닫혔다" 가 거짓이 된다 (`refs.py`)."""
        project = await _project(session, key="CLO")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 3)
        before = issue.state_id
        await record(session, repository, [_commit("fixes CLO-3")])
        (link,) = await _links(session, issue)
        assert link.closing is True
        assert issue.state_id == before

    async def test_a_redelivery_does_not_stack_a_second_row(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """웹훅은 다시 온다 — 재전송, 재시도, 사람이 누른 redeliver."""
        project = await _project(session, key="IDE")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 5)
        change = _commit("IDE-5 고친다")
        assert await record(session, repository, [change]) == 1
        assert await record(session, repository, [change]) == 0
        assert len(await _links(session, issue)) == 1

    async def test_a_redelivery_refreshes_the_title(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """PR 은 제목이 바뀐다. 목록이 옛 제목을 들고 있으면 눌러 보고서야
        안다."""
        project = await _project(session, key="TIT")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 6)
        first = Change(
            kind="pull_request",
            ref="9",
            title="초안: TIT-6",
            url="https://github.com/acme/web/pull/9",
            author="sujin",
            happened_at=utcnow() - timedelta(hours=2),
            text="TIT-6 준비",
        )
        await record(session, repository, [first])
        later = Change(
            kind="pull_request",
            ref="9",
            title="TIT-6 로그인 정리",
            url="https://github.com/acme/web/pull/9",
            author="sujin",
            happened_at=utcnow(),
            text="TIT-6 준비",
        )
        await record(session, repository, [later])
        (link,) = await _links(session, issue)
        assert link.title == "TIT-6 로그인 정리"
        assert link.happened_at > first.happened_at

    async def test_the_closing_mark_turns_on_but_never_off(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """두 번째 몸에 그 낱말이 없다고 꺼 버리면 **사람이 한 말을 우리가
        지우는 것**이다. 제목을 고친 PR 이 그 모양으로 온다."""
        project = await _project(session, key="ONO")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 7)
        plain = _commit("ONO-7 손본다", ref="f" * 40)
        await record(session, repository, [plain])
        closing = _commit("fixes ONO-7", ref="f" * 40)
        await record(session, repository, [closing])
        (link,) = await _links(session, issue)
        assert link.closing is True

        await record(session, repository, [plain])
        (again,) = await _links(session, issue)
        assert again.closing is True

    async def test_a_key_from_a_project_this_repository_does_not_list_is_ignored(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """**이 파일의 핵이다.** 시크릿을 아는 쪽이 설치 전체의 이슈에 글을
        붙일 수 있으면 그건 연동이 아니라 구멍이다."""
        mine = await _project(session, key="MIN")
        theirs = await _project(session, key="THR")
        owner = await _actor(session, [mine, theirs], perms.ALL)
        repository = await _repository(session, permissions, owner, [mine])
        outsider = await _issue(session, theirs, issue_type, state, 1)
        assert await record(session, repository, [_commit("THR-1 몰래 붙인다")]) == 0
        assert await _links(session, outsider) == []

    async def test_an_unknown_key_is_silently_skipped(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """오타, 지운 이슈, 아직 안 만든 것. 400 을 주면 코드 호스트가
        재전송을 반복하고 결국 사람이 웹훅을 끈다."""
        project = await _project(session, key="GHO")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        assert await record(session, repository, [_commit("GHO-9999 고친다")]) == 0

    async def test_text_without_any_key_is_no_link(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session, key="NOK")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        assert await record(session, repository, [_commit("UTF-8 인코딩을 고친다")]) == 0

    async def test_two_keys_in_one_commit_make_two_links(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        project = await _project(session, key="TWO")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        first = await _issue(session, project, issue_type, state, 1)
        second = await _issue(session, project, issue_type, state, 2)
        made = await record(session, repository, [_commit("TWO-1 과 TWO-2 를 함께 고친다")])
        assert made == 2
        assert len(await _links(session, first)) == 1
        assert len(await _links(session, second)) == 1

    async def test_the_same_key_twice_in_one_message_is_one_link(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        project = await _project(session, key="DBL")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 4)
        made = await record(session, repository, [_commit("DBL-4 고친다\n\nfixes DBL-4")])
        assert made == 1
        (link,) = await _links(session, issue)
        assert link.closing is True

    async def test_a_very_long_subject_still_fits(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """커밋 제목에 상한은 없다. 자르지 않으면 그 전송 하나가 500 이 되고,
        코드 호스트의 전송 로그에는 우리 서버 오류만 남는다."""
        project = await _project(session, key="LNG")
        owner = await _actor(session, [project], perms.ALL)
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 8)
        subject = "LNG-8 " + ("아주 긴 제목 " * 200)
        assert await record(session, repository, [_commit(subject)]) == 1
        (link,) = await _links(session, issue)
        assert len(link.title) <= 500

    async def test_a_repository_whose_projects_are_gone_records_nothing(
        self, session: AsyncSession
    ) -> None:
        """프로젝트를 지운 뒤에도 저장소 행은 남아 있을 수 있다. 그때 훑을
        키 목록이 비는데, 거기서 터지면 전송마다 500 이다."""
        row = Repository(
            provider="github",
            name=f"acme/{secrets.token_hex(4)}",
            secret_enc=secret_box().encrypt("x"),
            project_ids=[str(uuid4())],
        )
        session.add(row)
        await session.flush()
        assert await record(session, row, [_commit("ANY-1 고친다")]) == 0


class TestReadingLinks:
    async def test_the_newest_change_comes_first(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        project = await _project(session, key="ORD")
        owner = await _actor(session, [project], (*perms.ALL, "issue.view"))
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 2)
        old = _commit("ORD-2 처음", ref="1" * 40, when=utcnow() - timedelta(days=2))
        new = _commit("ORD-2 나중", ref="2" * 40)
        await record(session, repository, [old, new])
        found = await RepositoryService(session, permissions).links_for(owner, issue.id)
        assert [row.link.external_ref for row in found] == ["2" * 40, "1" * 40]
        assert found[0].repository_name == repository.name

    async def test_someone_who_cannot_see_the_issue_cannot_see_its_commits(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        issue_type: IssueType,
        state: WorkflowState,
    ) -> None:
        """커밋 제목은 이슈의 내용만큼 민감할 수 있다 — 보안 이슈의 수정
        커밋이 그렇다. 그래서 이슈와 같은 문을 쓴다."""
        project = await _project(session, key="SEC")
        owner = await _actor(session, [project], (*perms.ALL, "issue.view"))
        repository = await _repository(session, permissions, owner, [project])
        issue = await _issue(session, project, issue_type, state, 1)
        await record(session, repository, [_commit("SEC-1 고친다")])
        elsewhere = await _project(session)
        stranger = await _actor(session, [elsewhere], (*perms.ALL, "issue.view"))
        with pytest.raises(PermissionDeniedError):
            await RepositoryService(session, permissions).links_for(stranger, issue.id)

    async def test_a_missing_issue_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        actor = await _actor(session, [project], (*perms.ALL, "issue.view"))
        with pytest.raises(NotFoundError):
            await RepositoryService(session, permissions).links_for(actor, uuid4())
