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

    async def sibling_slug_taken(
        self, *, space_id: UUID, parent_id: UUID | None, slug: str, exclude: UUID | None = None
    ) -> bool:
        stmt = select(func.count()).select_from(Page).where(Page.slug == slug)
        if parent_id is None:
            stmt = stmt.where(Page.space_id == space_id).where(Page.parent_id.is_(None))
        else:
            stmt = stmt.where(Page.parent_id == parent_id)
        if exclude is not None:
            stmt = stmt.where(Page.id != exclude)
        return bool((await self._s.execute(stmt)).scalar_one())

    def add(self, page: Page) -> Page:
        self._s.add(page)
        return page

    async def children_of(self, page_id: UUID | None, space_id: UUID) -> list[Page]:
        stmt = select(Page).where(Page.archived_at.is_(None))
        if page_id is None:
            stmt = stmt.where(Page.space_id == space_id).where(Page.parent_id.is_(None))
        else:
            stmt = stmt.where(Page.parent_id == page_id)
        return list(
            (await self._s.execute(stmt.order_by(Page.position, Page.title))).scalars().all()
        )

    async def tree_of(self, space_id: UUID, *, include_archived: bool = False) -> list[Page]:
        """스페이스의 문서 전부. 화면이 부모-자식으로 조립한다.

        깊이마다 질의를 내면(재귀 로딩) 트리 깊이만큼 왕복한다. 한 스페이스의
        문서 수는 사람이 관리하는 규모라 한 번에 받아 조립하는 편이 낫다.
        """
        stmt = select(Page).where(Page.space_id == space_id)
        if not include_archived:
            stmt = stmt.where(Page.archived_at.is_(None))
        return list(
            (await self._s.execute(stmt.order_by(Page.path, Page.position, Page.title)))
            .scalars()
            .all()
        )

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
