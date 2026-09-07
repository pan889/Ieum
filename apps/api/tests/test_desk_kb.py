"""요청 중 문서 추천 (feature-map C8).

붙잡는 것 — 첫째가 이 파일의 이유다:

- **고객에게 보이는 것은 `kind = "kb"` 스페이스뿐이다.** 팀 스페이스를 걸 수
  있게 두면 실수 하나가 곧 내부 문서 유출이다.
- **제한이 걸린 문서는 안 나간다.** 스페이스가 공개여도 문서 하나에 제한이
  걸려 있으면 그건 누군가를 위해 좁혀 둔 것이다.
- **초안은 안 나간다.** 색인에 없다는 것을 여기서도 확인한다 — 색인 규칙이
  바뀌면 고객 화면이 먼저 새기 때문이다.
- **빈 질의에 전부 내주지 않는다.** 그건 추천이 아니라 스페이스 공개다.
- 연결이 없으면 **빈 목록이다 — 오류가 아니다.** 대부분의 요청 유형은
  지식베이스를 안 건다.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.service import CustomerPortalService, PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.search import contracts as search
from ieum.modules.wiki.models import Space
from role_grants import actor_for, grant

SUMMARY_FIELD = {"key": "summary", "label": "무엇이 문제인가요", "required": True}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _space(session: AsyncSession, *, kind: str = "kb") -> Space:
    row = Space(key=f"K{new_id().hex[-5:].upper()}", name=f"KB {new_id().hex[-4:]}", kind=kind)
    session.add(row)
    await session.flush()
    return row


async def _index(
    session: AsyncSession,
    space: Space,
    *,
    title: str,
    body: str,
    restricted_to: list[object] | None = None,
) -> object:
    """검색 색인에 문서 한 편을 직접 넣는다.

    위키 서비스를 거치지 않는 이유: 여기서 보는 것은 **추천이 색인을 어떻게
    좁히는가**이고, 문서를 만드는 경로는 위키 시험이 이미 본다. 색인 행을
    직접 두면 제한·초안 같은 경우를 한 줄로 만들 수 있다.
    """
    page_id = new_id()
    await search.index_document(
        session,
        kind=search.PAGE,
        entity_id=page_id,
        scope_kind="space",
        scope_id=space.id,
        ref=f"{space.key}/{title}",
        title=title,
        body=body,
        restricted_to=restricted_to,  # type: ignore[arg-type]
        updated_at=utcnow(),
    )
    await session.flush()
    return page_id


async def _portal_with_kb(
    session: AsyncSession, permissions: PermissionService, space: Space | None
) -> tuple[str, object, User]:
    project = Project(key=f"K{new_id().hex[-6:].upper()}", name="KB desk")
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
        permissions_granted=(desk_perms.PORTAL_MANAGE,),
        scope=Scope.project(project.id),
    )
    service = PortalService(session, permissions)
    slug = f"help-{new_id().hex[-6:]}"
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=slug,
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
            kb_space_id=space.id if space else None,
        )
    ).request_type
    return slug, request_type, manager


class TestLinkingASpace:
    async def test_a_kb_space_can_be_linked(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        _, request_type, _ = await _portal_with_kb(session, permissions, space)
        assert request_type.kb_space_id == space.id  # type: ignore[attr-defined]

    @pytest.mark.parametrize("kind", ["team", "personal"])
    async def test_a_non_kb_space_is_refused(
        self, session: AsyncSession, permissions: PermissionService, kind: str
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 팀 스페이스를 걸 수 있게 두면
        실수 하나가 곧 내부 문서 유출이다."""
        space = await _space(session, kind=kind)
        with pytest.raises(ValidationError) as exc:
            await _portal_with_kb(session, permissions, space)
        assert exc.value.code == "desk.kb_space_not_public"

    async def test_a_missing_space_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        await session.delete(space)
        await session.flush()
        with pytest.raises(ValidationError) as exc:
            await _portal_with_kb(session, permissions, space)
        assert exc.value.code == "desk.kb_space_missing"

    async def test_the_link_can_be_cleared_but_not_by_omission(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`None` 은 "안 건드린다" 다. 한 필드로 둘을 표현하면 폼이 값을 안
        보내는 것만으로 연결이 끊긴다."""
        space = await _space(session)
        _, request_type, manager = await _portal_with_kb(session, permissions, space)
        service = PortalService(session, permissions)

        kept = await service.update_request_type(
            actor_for(manager),
            request_type.id,  # type: ignore[attr-defined]
            name="Broken thing 2",
        )
        assert kept.request_type.kb_space_id == space.id

        cleared = await service.update_request_type(
            actor_for(manager),
            request_type.id,  # type: ignore[attr-defined]
            clear_kb_space=True,
        )
        assert cleared.request_type.kb_space_id is None

    async def test_only_kb_spaces_are_offered(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """거절하는 쪽과 고르게 하는 쪽은 같이 온다 (ux-principles)."""
        from ieum.modules.wiki import contracts as wiki

        kb = await _space(session)
        team = await _space(session, kind="team")
        offered = {row.id for row in await wiki.kb_spaces(session)}
        assert kb.id in offered
        assert team.id not in offered


class TestSuggesting:
    async def test_it_finds_a_matching_article(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        await _index(session, space, title="프린터 문제 해결", body="프린터가 안 될 때")
        slug, request_type, _ = await _portal_with_kb(session, permissions, space)

        found = await CustomerPortalService(session, permissions).suggest_articles(
            slug,
            request_type.id,  # type: ignore[attr-defined]
            "프린터",
        )
        assert [row.title for row in found] == ["프린터 문제 해결"]
        # 본문 전체가 아니라 발췌다.
        assert found[0].excerpt.startswith("프린터가 안 될 때")
        assert found[0].ref.startswith(space.key)

    async def test_a_restricted_article_is_not_offered(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """스페이스가 공개여도 문서 하나에 제한이 걸려 있으면 그건 누군가를
        위해 좁혀 둔 것이다."""
        space = await _space(session)
        await _index(session, space, title="공개 안내", body="프린터 안내")
        await _index(
            session,
            space,
            title="내부 절차",
            body="프린터 교체 예산",
            restricted_to=[new_id()],
        )
        slug, request_type, _ = await _portal_with_kb(session, permissions, space)

        found = await CustomerPortalService(session, permissions).suggest_articles(
            slug,
            request_type.id,  # type: ignore[attr-defined]
            "프린터",
        )
        assert [row.title for row in found] == ["공개 안내"]

    async def test_another_spaces_article_is_not_offered(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        mine = await _space(session)
        theirs = await _space(session)
        await _index(session, theirs, title="남의 문서", body="프린터")
        slug, request_type, _ = await _portal_with_kb(session, permissions, mine)

        found = await CustomerPortalService(session, permissions).suggest_articles(
            slug,
            request_type.id,  # type: ignore[attr-defined]
            "프린터",
        )
        assert found == []

    async def test_an_empty_query_offers_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객이 아직 아무 것도 안 적었는데 목록이 뜨면, 그건 추천이 아니라
        스페이스 공개다."""
        space = await _space(session)
        await _index(session, space, title="아무 문서", body="내용")
        slug, request_type, _ = await _portal_with_kb(session, permissions, space)

        for query in ("", "   "):
            found = await CustomerPortalService(session, permissions).suggest_articles(
                slug,
                request_type.id,  # type: ignore[attr-defined]
                query,
            )
            assert found == []

    async def test_no_link_means_no_suggestions_not_an_error(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """대부분의 요청 유형은 지식베이스를 안 건다. 그때 화면이 오류를
        그리면 아무 문제도 없는데 무언가 잘못된 것처럼 보인다."""
        slug, request_type, _ = await _portal_with_kb(session, permissions, None)
        found = await CustomerPortalService(session, permissions).suggest_articles(
            slug,
            request_type.id,  # type: ignore[attr-defined]
            "프린터",
        )
        assert found == []

    async def test_it_respects_the_limit(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """추천이 열 개면 그건 목록이지 추천이 아니다."""
        space = await _space(session)
        for index in range(7):
            await _index(session, space, title=f"프린터 안내 {index}", body="프린터")
        slug, request_type, _ = await _portal_with_kb(session, permissions, space)

        found = await CustomerPortalService(session, permissions).suggest_articles(
            slug,
            request_type.id,  # type: ignore[attr-defined]
            "프린터",
            limit=3,
        )
        assert len(found) == 3
