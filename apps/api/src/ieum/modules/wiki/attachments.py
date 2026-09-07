"""문서 첨부의 권한 판정.

core 는 첨부 테이블만 소유하고 권한은 모른다. 소유자 종류마다 여기서
리졸버를 등록한다 (core/attachments.py 참고).

**문서 제한(restriction)까지 본다.** 스페이스 권한만 보면, 제한이 걸린 문서에
붙은 그림을 아무나 열 수 있다 — 본문은 가리면서 그림은 열어 두면 가린 의미가
없다. 이슈의 내부 노트 첨부와 같은 규칙이다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.attachments import register_owner
from ieum.core.context import Actor
from ieum.core.permissions import Scope, get_permission_service
from ieum.modules.wiki import permissions as perms
from ieum.modules.wiki.models import Page

OWNER_PAGE = "page"


class PageAttachments:
    """문서에 붙는 첨부. 문서를 볼 수 있으면 첨부도 볼 수 있다."""

    async def _page(self, session: AsyncSession, owner_id: UUID) -> Page | None:
        page = await session.get(Page, owner_id)
        return None if page is None or page.is_archived else page

    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        page = await self._page(session, owner_id)
        if page is None:
            return False
        return await get_permission_service().has(
            session,
            actor,
            perms.PAGE_VIEW,
            scope=Scope.space(page.space_id),
            subject=page,
        )

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        """파일을 붙이는 건 문서를 고치는 것과 같은 무게다."""
        page = await self._page(session, owner_id)
        if page is None:
            return False
        return await get_permission_service().has(
            session,
            actor,
            perms.PAGE_EDIT,
            scope=Scope.space(page.space_id),
            subject=page,
        )


def install() -> None:
    """기동 시 한 번 부른다."""
    register_owner(OWNER_PAGE, PageAttachments())


__all__ = ["OWNER_PAGE", "PageAttachments", "install"]
