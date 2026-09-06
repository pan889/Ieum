"""wiki 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다.

반환 타입은 ORM 모델이 아니라 DTO 다 (module-guide 모듈 간 통신 1번).
예외는 `page_model()` — core 의 권한 가드 등록에 타입 자체가 필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.wiki.models import Page
from ieum.modules.wiki.repository import PageRepository, SpaceRepository


@dataclass(frozen=True, slots=True)
class PageRef:
    """다른 모듈이 문서를 참조할 때 쓰는 최소 정보."""

    id: UUID
    space_id: UUID
    space_key: str
    path: str
    title: str


def page_model() -> type[Page]:
    """core 의 권한 가드 등록에 쓸 타입. 인스턴스를 노출하지 않는다."""
    return Page


async def get_page(session: AsyncSession, page_id: UUID) -> PageRef | None:
    page = await PageRepository(session).get(page_id)
    if page is None:
        return None
    space = await SpaceRepository(session).get(page.space_id)
    return PageRef(
        id=page.id,
        space_id=page.space_id,
        space_key=space.key if space else "",
        path=page.path,
        title=page.title,
    )


__all__ = ["PageRef", "get_page", "page_model"]
