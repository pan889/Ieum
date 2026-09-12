"""묶음을 실제로 적재한다.

**이 파일이 지키는 것 셋.**

1. 두 번 돌려도 안 늘어난다. 이관은 한 번에 안 끝나고, 중간에 멈춘 뒤 다시
   돌리는 것이 정상이다.
2. 이력이 살아 온다. 3년치 이슈가 전부 오늘 만들어진 것이 되면 **그 이력이
   이관의 목적**인데 그것을 잃는다.
3. 못 옮긴 것을 센다. 들어온 개수는 나중에도 셀 수 있지만, 안 들어온 것은
   여기서 안 적으면 아무 데도 안 남는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import ConflictError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.migrate.archive import (
    Archive,
    Comment,
    Issue,
    Manifest,
    Person,
    Relation,
    Source,
    write_archive,
)
from ieum.migrate.archive import Project as SourceProject
from ieum.modules.identity.models import User
from ieum.modules.imports import permissions as perms
from ieum.modules.imports.models import ImportedObject
from ieum.modules.imports.service import ImportService
from ieum.modules.issues.models import Issue as IssueRow
from ieum.modules.issues.models import IssueComment, IssueLabel, IssueLink
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


async def _admin(session: AsyncSession) -> User:
    person = User(email=f"ld-{new_id()}@example.com", display_name="이관 담당", status="active")
    session.add(person)
    await session.flush()
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=(perms.IMPORT_RUN,),
        scope=Scope.global_(),
    )
    return person


async def _project(session: AsyncSession) -> Project:
    """프로젝트 하나와 **그 프로젝트가 쓸 어휘**.

    시험 세션의 스키마는 비어 있다(시드가 안 돈다). 유형·상태를 여기서
    만드는 편이 오히려 낫다 — 이 파일이 견주는 낱말이 무엇인지 눈에 보인다.
    """
    from ieum.modules.issues.models import IssueType, Workflow, WorkflowState

    row = Project(key=f"L{new_id().hex[:6].upper()}", name="받는 프로젝트")
    session.add(row)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    for position, (name, category, initial) in enumerate(
        (("Open", "todo", True), ("Closed", "done", False))
    ):
        session.add(
            WorkflowState(
                workflow_id=workflow.id,
                name=name,
                category=category,
                position=position,
                is_initial=initial,
            )
        )
    # 전역 유형(`project_id` 가 NULL)이라 어느 프로젝트에서나 쓴다. 하위작업
    # 전용도 하나 둔다 — 후보에서 빠지는 것을 여기서도 확인할 수 있게.
    session.add(IssueType(project_id=None, name="Bug", workflow_id=workflow.id))
    session.add(
        IssueType(project_id=None, name="Sub-task", workflow_id=workflow.id, is_subtask=True)
    )
    await session.flush()
    return row


def _bundle(issues: list[Issue], people: list[Person] | None = None) -> bytes:
    return write_archive(
        Archive(
            manifest=Manifest(
                source=Source(kind="redmine", base_url="https://redmine.example.com"),
                project=SourceProject(key="p", name="소스"),
                taken_at="2026-09-12T00:00:00Z",
            ),
            people=people or [],
            issues=issues,
        )
    )


def _two_issues() -> list[Issue]:
    """부모자식과 관계가 있는 최소한의 묶음. 상태는 시드 어휘에 있는 것으로."""
    return [
        Issue(
            source_id="1",
            summary="부모",
            type="Bug",
            status="Open",
            priority="High",
            created_at="2023-04-05T06:07:08Z",
            comments=[Comment(source_id="c1", body="옛날 댓글", created_at="2023-04-06T00:00:00Z")],
            relations=[Relation(kind="copied_to", target="2")],
        ),
        Issue(
            source_id="2",
            summary="자식",
            type="Bug",
            status="Closed",
            priority="Low",
            parent="1",
            created_at="2023-05-01T00:00:00Z",
        ),
    ]


async def _count(session: AsyncSession, model: type, column: object, value: object) -> int:
    stmt = select(func.count()).select_from(model).where(column == value)  # type: ignore[arg-type]
    return int((await session.execute(stmt)).scalar_one())


class TestPaddedWordsDoNotBlowUpHalfway:
    """**미리 보기는 통과시키고 적재가 500 으로 죽었다.**

    어휘를 모을 때는 `strip` 하고 찾을 때는 원본 문자열을 썼다. 소스의 상태
    이름에 앞뒤 공백이 하나라도 있으면 `KeyError` 가 나는데, 그것도 이슈를
    절반쯤 만든 뒤에 난다 — 되돌릴 수 없는 자리다.
    """

    async def test_a_status_with_stray_spaces_still_loads(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)
        data = _bundle(
            [
                Issue(
                    source_id="1",
                    summary="공백 낀 상태",
                    type=" Bug ",
                    status=" Open ",
                    priority=" High ",
                    created_at="2023-04-05T06:07:08Z",
                )
            ]
        )

        done = await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=data
        )
        assert (done.issues_created, done.issues_skipped) == (1, 0)

        issue = (
            await session.execute(select(IssueRow).where(IssueRow.project_id == project.id))
        ).scalar_one()
        assert issue.summary == "공백 낀 상태"
        # 다듬은 이름으로 제대로 이었다 — 2 는 High 다.
        assert issue.priority == 2


class TestItRunsTwiceWithoutGrowing:
    async def test_the_second_run_creates_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 이관은 한 번에 안 끝난다."""
        person = await _admin(session)
        project = await _project(session)
        service = ImportService(session, permissions)
        data = _bundle(_two_issues())

        first = await service.load(actor_for(person), project_id=project.id, data=data)
        assert (first.issues_created, first.issues_skipped) == (2, 0)
        assert first.comments_created == 1

        second = await service.load(actor_for(person), project_id=project.id, data=data)
        assert (second.issues_created, second.issues_skipped) == (0, 2)
        assert second.comments_created == 0

        assert await _count(session, IssueRow, IssueRow.project_id, project.id) == 2

    async def test_comments_have_their_own_key(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이슈만 세면 **중간에 멈춘 실행**에서 코멘트가 두 벌이 된다."""
        person = await _admin(session)
        project = await _project(session)
        service = ImportService(session, permissions)
        data = _bundle(_two_issues())

        await service.load(actor_for(person), project_id=project.id, data=data)
        await service.load(actor_for(person), project_id=project.id, data=data)

        issue = (
            (await session.execute(select(IssueRow).where(IssueRow.project_id == project.id)))
            .scalars()
            .first()
        )
        assert issue is not None
        total = (
            await session.execute(
                select(func.count())
                .select_from(IssueComment)
                .join(IssueRow, IssueRow.id == IssueComment.issue_id)
                .where(IssueRow.project_id == project.id)
            )
        ).scalar_one()
        assert total == 1

    async def test_it_remembers_both_kinds(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)
        await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(_two_issues())
        )
        kinds = (
            (
                await session.execute(
                    select(ImportedObject.target_type).where(
                        ImportedObject.project_id == project.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(set(kinds)) == ["comment", "issue"]


class TestTheHistoryComesAcross:
    async def test_the_created_time_is_the_source_time_not_today(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 값이 이관의 목적이다.** 전부 오늘로 들어오면 3년치 이력이 사라진다."""
        person = await _admin(session)
        project = await _project(session)
        await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(_two_issues())
        )
        row = (
            await session.execute(
                select(IssueRow).where(IssueRow.project_id == project.id, IssueRow.key_seq == 1)
            )
        ).scalar_one()
        assert row.created_at == datetime(2023, 4, 5, 6, 7, 8, tzinfo=UTC)

    async def test_a_closed_issue_lands_closed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """소스의 `Closed` 는 우리가 지금 수행하는 전이가 아니라 **이미 일어난
        사실**이다. 시작 상태로 넣고 전이를 흉내 내면 오늘 날짜의 가짜 전이가
        이력에 쌓인다."""
        from ieum.modules.issues.models import WorkflowState

        person = await _admin(session)
        project = await _project(session)
        await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(_two_issues())
        )
        rows = (
            await session.execute(
                select(IssueRow.summary, WorkflowState.name)
                .join(WorkflowState, WorkflowState.id == IssueRow.state_id)
                .where(IssueRow.project_id == project.id)
            )
        ).all()
        assert dict(rows) == {"부모": "Open", "자식": "Closed"}


class TestLinks:
    async def test_the_parent_is_linked_after_everything_exists(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """부모가 자식보다 **뒤에** 올 수 있다. 만들면서 이으려 하면 없는
        것을 가리키고, 그때 건너뛰면 그 관계는 영영 안 생긴다."""
        person = await _admin(session)
        project = await _project(session)
        # 자식이 먼저 오는 묶음.
        pair = list(reversed(_two_issues()))
        loaded = await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(pair)
        )
        assert loaded.parents_linked == 1
        child = (
            await session.execute(
                select(IssueRow).where(
                    IssueRow.project_id == project.id, IssueRow.summary == "자식"
                )
            )
        ).scalar_one()
        assert child.parent_id is not None

    async def test_copied_to_becomes_copied(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**묶음은 `copied_to`, 우리는 `copied` 다.** 표가 없으면 이관
        한가운데서 CHECK 제약에 걸리고 절반만 들어온 상태가 남는다."""
        person = await _admin(session)
        project = await _project(session)
        loaded = await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(_two_issues())
        )
        assert loaded.relations_linked == 1
        kinds = (
            (
                await session.execute(
                    select(IssueLink.kind)
                    .join(IssueRow, IssueRow.id == IssueLink.from_issue_id)
                    .where(IssueRow.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert list(kinds) == ["copied"]

    async def test_an_unknown_relation_kind_is_named_not_swallowed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)
        issues = _two_issues()
        issues[0] = Issue(
            source_id="1",
            summary="부모",
            type="Bug",
            status="Open",
            relations=[Relation(kind="follows", target="2")],
        )
        loaded = await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(issues)
        )
        assert loaded.relations_linked == 0
        assert any("follows" in row for row in loaded.unmoved)


class TestPeopleAndLabels:
    async def test_a_person_we_know_becomes_the_reporter(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)
        issues = [Issue(source_id="1", summary="내 것", type="Bug", status="Open", author="u1")]
        loaded = await ImportService(session, permissions).load(
            actor_for(person),
            project_id=project.id,
            data=_bundle(issues, [Person(source_id="u1", name="나", email=person.email)]),
        )
        row = (
            await session.execute(select(IssueRow).where(IssueRow.project_id == project.id))
        ).scalar_one()
        assert row.reporter_id == person.id
        assert loaded.unmoved == []

    async def test_a_person_we_do_not_know_is_named_in_the_report(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """개수만 세면 **어느 이슈가 작성자를 잃었는지** 못 찾는다."""
        person = await _admin(session)
        project = await _project(session)
        issues = [Issue(source_id="7", summary="남의 것", type="Bug", status="Open", author="u9")]
        loaded = await ImportService(session, permissions).load(
            actor_for(person),
            project_id=project.id,
            data=_bundle(issues, [Person(source_id="u9", name="모르는 사람", email="")]),
        )
        assert any("작성자를 못 이었다: 7" in row for row in loaded.unmoved)

    async def test_labels_come_across(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person = await _admin(session)
        project = await _project(session)
        issues = [
            Issue(
                source_id="1",
                summary="라벨",
                type="Bug",
                status="Open",
                labels=["급함", "회귀"],
            )
        ]
        await ImportService(session, permissions).load(
            actor_for(person), project_id=project.id, data=_bundle(issues)
        )
        row = (
            await session.execute(select(IssueRow).where(IssueRow.project_id == project.id))
        ).scalar_one()
        labels = (
            (await session.execute(select(IssueLabel.label).where(IssueLabel.issue_id == row.id)))
            .scalars()
            .all()
        )
        assert sorted(labels) == ["급함", "회귀"]


class TestItRefusesRatherThanHalfLoading:
    async def test_an_unmapped_status_stops_everything(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**절반만 들어온 상태가 가장 나쁘다.** 되돌리려면 무엇이 들어왔는지
        세어야 하는데, 그걸 세려고 이 표를 만든 것이다."""
        person = await _admin(session)
        project = await _project(session)
        issues = [Issue(source_id="1", summary="x", type="Bug", status="New")]

        with pytest.raises(ConflictError):
            await ImportService(session, permissions).load(
                actor_for(person), project_id=project.id, data=_bundle(issues)
            )
        assert await _count(session, IssueRow, IssueRow.project_id, project.id) == 0

    async def test_a_blank_status_stops_everything_too(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """빈 칸은 짝지을 낱말이 없다. 우리가 골라 주면 그 이슈들이 아무도 안
        고른 자리로 들어가고, **개수는 맞아서 옮긴 사람은 성공으로 본다.**"""
        person = await _admin(session)
        project = await _project(session)
        issues = [Issue(source_id="1", summary="x", type="Bug", status="")]

        with pytest.raises(ConflictError):
            await ImportService(session, permissions).load(
                actor_for(person), project_id=project.id, data=_bundle(issues)
            )
        assert await _count(session, IssueRow, IssueRow.project_id, project.id) == 0
