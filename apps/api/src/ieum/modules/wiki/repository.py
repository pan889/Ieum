"""wiki 데이터 접근. 쿼리만 한다."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import Page as PageResult
from ieum.core.pagination import PageRequest
from ieum.core.permissions import Acl
from ieum.modules.wiki.models import (
    Page,
    PageComment,
    PageDraft,
    PageLabel,
    PageRestriction,
    PageTask,
    PageTemplate,
    PageVersion,
    Space,
)


class SpaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, space_id: UUID) -> Space | None:
        return await self._s.get(Space, space_id)

    async def get_by_key(self, key: str) -> Space | None:
        stmt = select(Space).where(Space.key == key.upper())
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def key_exists(self, key: str) -> bool:
        stmt = select(func.count()).select_from(Space).where(Space.key == key.upper())
        return bool((await self._s.execute(stmt)).scalar_one())

    def add(self, space: Space) -> Space:
        self._s.add(space)
        return space

    async def list_page(
        self,
        request: PageRequest,
        *,
        acl: Acl,
        include_archived: bool = False,
        query: str | None = None,
    ) -> PageResult[Space]:
        """목록은 검사하지 않고 필터링한다 (auth.md 5절)."""
        if acl.is_empty:
            return PageResult(items=[])

        stmt: Select[tuple[Space]] = select(Space)
        if not acl.is_global:
            stmt = stmt.where(Space.id.in_(acl.space_ids))
        if not include_archived:
            stmt = stmt.where(Space.archived_at.is_(None))
        if query:
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(func.lower(Space.key).like(like) | func.lower(Space.name).like(like))

        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(Space.key > payload["key"])
        stmt = stmt.order_by(Space.key).limit(request.fetch_limit)

        rows = list((await self._s.execute(stmt)).scalars().all())
        return PageResult.from_rows(rows, request, lambda s: {"key": s.key})


class PageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, page_id: UUID) -> Page | None:
        return await self._s.get(Page, page_id)

    async def get_by_path(self, space_id: UUID, path: str) -> Page | None:
        stmt = select(Page).where(Page.space_id == space_id).where(Page.path == path)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def sibling_slugs(
        self,
        *,
        space_id: UUID,
        parent_id: UUID | None,
        exclude: UUID | None = None,
        kind: str = "page",
    ) -> set[str]:
        """유일 인덱스가 막는 slug **전부**. 새 slug 을 고를 때 이걸 본다.

        `children_of` 를 쓰면 안 된다 — 거기는 보관된 문서를 뺀다. 인덱스는
        안 뺀다. 그래서 보관된 형제와 같은 제목으로 문서를 만들면, 화면에는
        보이지도 않는 행과 부딪혀 **500** 이 난다(무엇과 부딪혔는지 말해 줄
        수도 없다 — 안 보이는 문서니까).

        조건은 `uq_page_parent_slug` / `uq_page_space_root_slug` 와 **글자
        그대로 같아야** 한다. 여기가 인덱스보다 느슨하면 500 이 나고, 빡빡하면
        멀쩡한 제목에 `-2` 가 붙는다.
        """
        stmt = select(Page.slug)
        if parent_id is None:
            stmt = (
                stmt.where(Page.space_id == space_id)
                .where(Page.parent_id.is_(None))
                .where(Page.kind == kind)
            )
        else:
            stmt = stmt.where(Page.parent_id == parent_id)
        if exclude is not None:
            stmt = stmt.where(Page.id != exclude)
        return set((await self._s.execute(stmt)).scalars().all())

    def add(self, page: Page) -> Page:
        self._s.add(page)
        return page

    async def children_of(self, page_id: UUID | None, space_id: UUID) -> list[Page]:
        # 블로그 글은 트리에 없다. 날짜순으로 흐르는 글이라 위치가 없다.
        stmt = select(Page).where(Page.archived_at.is_(None)).where(Page.kind == "page")
        if page_id is None:
            stmt = stmt.where(Page.space_id == space_id).where(Page.parent_id.is_(None))
        else:
            stmt = stmt.where(Page.parent_id == page_id)
        return list(
            (await self._s.execute(stmt.order_by(Page.position, Page.title))).scalars().all()
        )

    async def tree_of(
        self, space_id: UUID, *, include_archived: bool = False, kind: str | None = "page"
    ) -> list[Page]:
        """스페이스의 문서 전부. 화면이 부모-자식으로 조립한다.

        깊이마다 질의를 내면(재귀 로딩) 트리 깊이만큼 왕복한다. 한 스페이스의
        문서 수는 사람이 관리하는 규모라 한 번에 받아 조립하는 편이 낫다.
        """
        stmt = select(Page).where(Page.space_id == space_id)
        if kind is not None:
            stmt = stmt.where(Page.kind == kind)
        if not include_archived:
            stmt = stmt.where(Page.archived_at.is_(None))
        return list(
            (await self._s.execute(stmt.order_by(Page.path, Page.position, Page.title)))
            .scalars()
            .all()
        )

    async def blog_slugs(self, space_id: UUID) -> list[Page]:
        """이 스페이스의 블로그 글 전부. 새 글의 slug 를 겹치지 않게 고를 때 쓴다."""
        stmt = select(Page).where(Page.space_id == space_id).where(Page.kind == "blog")
        return list((await self._s.execute(stmt)).scalars().all())

    async def blog_of(self, space_id: UUID, *, limit: int, offset: int = 0) -> list[Page]:
        """블로그 글, 최신순. 초안은 쓴 사람 말고는 볼 것이 없으므로 뺀다."""
        stmt = (
            select(Page)
            .where(Page.space_id == space_id)
            .where(Page.kind == "blog")
            .where(Page.archived_at.is_(None))
            .where(Page.status == "published")
            .order_by(Page.published_at.desc(), Page.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def blog_count(self, space_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Page)
            .where(Page.space_id == space_id)
            .where(Page.kind == "blog")
            .where(Page.archived_at.is_(None))
            .where(Page.status == "published")
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def by_ids(self, page_ids: Sequence[UUID]) -> list[Page]:
        if not page_ids:
            return []
        stmt = select(Page).where(Page.id.in_(list(page_ids)))
        return list((await self._s.execute(stmt)).scalars().all())

    async def by_paths(self, space_id: UUID, paths: Sequence[str]) -> list[Page]:
        """경로로 여러 문서를 한 번에. 조상 사슬을 가져올 때 쓴다."""
        if not paths:
            return []
        stmt = (
            select(Page)
            .where(Page.space_id == space_id)
            .where(Page.path.in_(list(paths)))
            .order_by(Page.path)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def subtree(self, space_id: UUID, path: str) -> list[Page]:
        """자기 자신 + 모든 후손. 경로 접두사로 한 번에 긁는다."""
        stmt = (
            select(Page)
            .where(Page.space_id == space_id)
            .where((Page.path == path) | Page.path.startswith(f"{path}/"))
            .order_by(Page.path)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def list_page(
        self,
        request: PageRequest,
        *,
        acl: Acl,
        space_id: UUID | None = None,
        include_archived: bool = False,
    ) -> PageResult[Page]:
        if acl.is_empty:
            return PageResult(items=[])

        stmt: Select[tuple[Page]] = select(Page)
        if not acl.is_global:
            stmt = stmt.where(Page.space_id.in_(acl.space_ids))
        if space_id is not None:
            stmt = stmt.where(Page.space_id == space_id)
        if not include_archived:
            stmt = stmt.where(Page.archived_at.is_(None))

        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(Page.id > UUID(payload["id"]))
        stmt = stmt.order_by(Page.id).limit(request.fetch_limit)

        rows = list((await self._s.execute(stmt)).scalars().all())
        return PageResult.from_rows(rows, request, lambda p: {"id": str(p.id)})


class PageVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, version_id: UUID) -> PageVersion | None:
        return await self._s.get(PageVersion, version_id)

    def add(self, version: PageVersion) -> PageVersion:
        self._s.add(version)
        return version

    async def next_number(self, page_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(PageVersion.number), 0)).where(
            PageVersion.page_id == page_id
        )
        return int((await self._s.execute(stmt)).scalar_one()) + 1

    async def history(self, page_id: UUID, *, limit: int = 50) -> list[PageVersion]:
        stmt = (
            select(PageVersion)
            .where(PageVersion.page_id == page_id)
            .order_by(PageVersion.number.desc())
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def by_number(self, page_id: UUID, number: int) -> PageVersion | None:
        stmt = (
            select(PageVersion)
            .where(PageVersion.page_id == page_id)
            .where(PageVersion.number == number)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()


class PageLabelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_page(self, page_id: UUID) -> list[str]:
        stmt = select(PageLabel.label).where(PageLabel.page_id == page_id).order_by(PageLabel.label)
        return list((await self._s.execute(stmt)).scalars().all())

    async def replace(self, page_id: UUID, labels: Sequence[str]) -> None:
        await self._s.execute(delete(PageLabel).where(PageLabel.page_id == page_id))
        for label in dict.fromkeys(labels):
            self._s.add(PageLabel(page_id=page_id, label=label))


class PageRestrictionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_page(self, page_id: UUID) -> list[PageRestriction]:
        stmt = select(PageRestriction).where(PageRestriction.page_id == page_id)
        return list((await self._s.execute(stmt)).scalars().all())

    async def for_pages(self, page_ids: Sequence[UUID]) -> dict[UUID, list[PageRestriction]]:
        """여러 문서의 제한을 한 번에. 트리 필터링이 행마다 조회하지 않게 한다."""
        if not page_ids:
            return {}
        stmt = select(PageRestriction).where(PageRestriction.page_id.in_(list(page_ids)))
        grouped: dict[UUID, list[PageRestriction]] = {}
        for row in (await self._s.execute(stmt)).scalars().all():
            grouped.setdefault(row.page_id, []).append(row)
        return grouped

    def add(self, restriction: PageRestriction) -> PageRestriction:
        self._s.add(restriction)
        return restriction

    async def clear(self, page_id: UUID, mode: str) -> None:
        await self._s.execute(
            delete(PageRestriction)
            .where(PageRestriction.page_id == page_id)
            .where(PageRestriction.mode == mode)
        )


class PageCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, comment_id: UUID) -> PageComment | None:
        return await self._s.get(PageComment, comment_id)

    def add(self, comment: PageComment) -> PageComment:
        self._s.add(comment)
        return comment

    async def for_page(self, page_id: UUID) -> list[PageComment]:
        stmt = (
            select(PageComment)
            .where(PageComment.page_id == page_id)
            .order_by(PageComment.created_at)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def anchored(self, page_id: UUID) -> list[PageComment]:
        """인라인 코멘트만. 재앵커링이 이걸 훑는다."""
        stmt = (
            select(PageComment)
            .where(PageComment.page_id == page_id)
            .where(PageComment.anchor.isnot(None))
        )
        return list((await self._s.execute(stmt)).scalars().all())


class PageTaskRepository:
    """본문에서 유도한 태스크 행. **읽고 통째로 다시 쓴다.**

    한 줄씩 갱신하지 않는 이유: 본문이 바뀌면 줄 번호가 통째로 밀린다. 그때
    "바뀐 것만" 찾아 고치려면 옛 본문과 새 본문을 맞춰 봐야 하는데, 그 계산이
    틀리면 유령 태스크가 남는다. 문서 하나의 태스크는 많아야 200개다
    (`markdown.tasks.MAX_TASKS`) — 지우고 다시 넣는 값이 싸다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def for_page(self, page_id: UUID) -> list[PageTask]:
        stmt = select(PageTask).where(PageTask.page_id == page_id).order_by(PageTask.line)
        return list((await self._s.execute(stmt)).scalars().all())

    async def replace(self, page_id: UUID, rows: Sequence[PageTask]) -> None:
        await self._s.execute(delete(PageTask).where(PageTask.page_id == page_id))
        for row in rows:
            self._s.add(row)
        await self._s.flush()

    async def assigned_to(
        self,
        *,
        assignee_id: UUID,
        acl: Acl,
        include_done: bool,
        limit: int,
    ) -> list[tuple[PageTask, Page, Space]]:
        """이 사람의 태스크. **볼 수 있는 문서의 것만.**

        `acl` 로 스페이스를 좁히고, 문서 단위 제한은 서비스가 한 번 더 거른다
        (`PageRestrictionGuard` 와 같은 규칙). 여기서 다 하려면 제한 상속을
        SQL 로 다시 구현해야 하고, 그러면 규칙이 두 벌이 된다.

        기한 없는 것을 뒤로 보낸다: 기한이 있는 것이 먼저 급하다. `NULLS LAST`
        를 안 쓰면 Postgres 는 오름차순에서 NULL 을 마지막에 두지만, 그것은
        기본값에 기대는 것이라 정렬 방향을 바꾸면 조용히 뒤집힌다.
        """
        if acl.is_empty:
            return []
        stmt = (
            select(PageTask, Page, Space)
            .join(Page, Page.id == PageTask.page_id)
            .join(Space, Space.id == Page.space_id)
            .where(
                PageTask.assignee_id == assignee_id,
                Page.archived_at.is_(None),
                Space.archived_at.is_(None),
            )
            .order_by(PageTask.due_date.asc().nullslast(), Page.title, PageTask.line)
            .limit(limit)
        )
        if not acl.is_global:
            stmt = stmt.where(Page.space_id.in_(acl.space_ids))
        if not include_done:
            stmt = stmt.where(PageTask.done.is_(False))
        return [(row[0], row[1], row[2]) for row in (await self._s.execute(stmt)).all()]


class PageTemplateRepository:
    """문서 템플릿. 스페이스 전용이거나 전역이다."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, template_id: UUID) -> PageTemplate | None:
        return await self._s.get(PageTemplate, template_id)

    def add(self, template: PageTemplate) -> PageTemplate:
        self._s.add(template)
        return template

    async def for_space(self, space_id: UUID) -> list[PageTemplate]:
        """그 스페이스 것 + 전역. 전역이 뒤에 온다 — 가까운 것이 먼저 보여야 한다."""
        stmt = (
            select(PageTemplate)
            .where(or_(PageTemplate.space_id == space_id, PageTemplate.space_id.is_(None)))
            .order_by(PageTemplate.space_id.is_(None), PageTemplate.name)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def delete(self, template: PageTemplate) -> None:
        await self._s.delete(template)


class PageDraftRepository:
    """저장하지 않은 편집. 사람마다 문서마다 하나."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, page_id: UUID, author_id: UUID) -> PageDraft | None:
        stmt = select(PageDraft).where(
            PageDraft.page_id == page_id, PageDraft.author_id == author_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, draft: PageDraft) -> PageDraft:
        self._s.add(draft)
        return draft

    async def clear(self, page_id: UUID, author_id: UUID) -> None:
        await self._s.execute(
            delete(PageDraft).where(PageDraft.page_id == page_id, PageDraft.author_id == author_id)
        )
