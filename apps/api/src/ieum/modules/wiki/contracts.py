"""wiki 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다.

반환 타입은 ORM 모델이 아니라 DTO 다 (module-guide 모듈 간 통신 1번).
예외는 `page_model()` — core 의 권한 가드 등록에 타입 자체가 필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.wiki import permissions as perms
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


__all__ = ["PageRef", "can_view_page", "can_view_space", "get_page", "page_model"]


async def can_view_page(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    page_id: UUID,
) -> bool:
    """이 사람이 이 문서를 볼 수 있나. **열람 제한까지 본다.**

    권한 이름·스코프·제한을 어떻게 엮는지는 이 모듈이 안다. 바깥에서
    조립하면 규칙이 바뀔 때 그쪽만 옛것이 된다.
    """
    page = await PageRepository(session).get(page_id)
    if page is None:
        return False
    return await permissions.has(
        session,
        actor,
        perms.PAGE_VIEW,
        scope=Scope.space(page.space_id),
        subject=page,
    )


async def can_view_space(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    space_id: UUID,
) -> bool:
    """이 사람이 이 스페이스를 볼 수 있나. 문서 하나하나가 아니라 스페이스 단위다."""
    space = await SpaceRepository(session).get(space_id)
    if space is None:
        return False
    return await permissions.has(session, actor, perms.PAGE_VIEW, scope=Scope.space(space_id))


async def space_kind(session: AsyncSession, space_id: UUID) -> str | None:
    """스페이스의 종류(`team`·`personal`·`kb`). 없으면 `None`.

    `desk` 가 요청 유형에 지식베이스를 걸 때 부른다 — **`kb` 만 걸 수 있다**
    (C8). 종류를 내주는 것으로 충분하고, 스페이스 객체를 내주면 다른 모듈이
    거기 붙은 것들까지 만지게 된다.
    """
    from ieum.modules.wiki.models import Space

    row = await session.get(Space, space_id)
    return None if row is None or row.archived_at is not None else row.kind


@dataclass(frozen=True, slots=True)
class SpaceRef:
    """고를 수 있는 스페이스 하나. 관리 화면의 선택 목록이 쓴다."""

    id: UUID
    key: str
    name: str


async def kb_spaces(session: AsyncSession) -> list[SpaceRef]:
    """고객에게 보여 줄 수 있는 스페이스들.

    **거절하는 쪽과 고르게 하는 쪽은 같이 온다** (ux-principles). `kb` 가
    아닌 것을 거절하도록 만들었으니, 화면에는 `kb` 만 보여야 한다.
    """
    from sqlalchemy import select

    from ieum.modules.wiki.models import Space

    rows = (
        await session.execute(
            select(Space)
            .where(Space.kind == "kb", Space.archived_at.is_(None))
            .order_by(Space.name)
        )
    ).scalars()
    return [SpaceRef(id=row.id, key=row.key, name=row.name) for row in rows]
