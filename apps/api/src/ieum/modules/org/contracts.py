"""org 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import PageRequest
from ieum.core.permissions import Acl
from ieum.modules.org.models import EntityLink, Project
from ieum.modules.org.repository import ProjectRepository


@dataclass(frozen=True, slots=True)
class ProjectRef:
    id: UUID
    key: str
    name: str
    is_archived: bool
    parent_id: UUID | None


def _to_ref(project: Project) -> ProjectRef:
    return ProjectRef(
        id=project.id,
        key=project.key,
        name=project.name,
        is_archived=project.is_archived,
        parent_id=project.parent_id,
    )


async def get_project(session: AsyncSession, project_id: UUID) -> ProjectRef | None:
    project = await ProjectRepository(session).get(project_id)
    return _to_ref(project) if project else None


async def get_projects(
    session: AsyncSession, project_ids: Iterable[UUID]
) -> dict[UUID, ProjectRef]:
    """여러 프로젝트를 한 번에. 목록 화면이 행마다 조회하지 않게 한다."""
    unique = list(dict.fromkeys(project_ids))
    if not unique:
        return {}
    rows = await ProjectRepository(session).get_many(unique)
    return {row.id: _to_ref(row) for row in rows}


async def get_project_by_key(session: AsyncSession, key: str) -> ProjectRef | None:
    project = await ProjectRepository(session).get_by_key(key)
    return _to_ref(project) if project else None


async def search_projects(
    session: AsyncSession, *, acl: Acl, query: str | None = None, limit: int = 20
) -> list[ProjectRef]:
    """볼 수 있는 프로젝트를 키·이름으로 찾는다. IQL 자동완성이 쓴다.

    목록은 검사하지 않고 필터링한다 (auth.md 5절) — 없는 것과 못 보는 것을
    화면에서 구별할 수 없게 한다.
    """
    page = await ProjectRepository(session).list_page(
        PageRequest(limit=limit), acl=acl, query=query
    )
    return [_to_ref(row) for row in page.items]


async def next_issue_number(session: AsyncSession, project_id: UUID) -> int:
    """이슈 키 채번. UPDATE ... RETURNING 으로 원자적으로 올린다 (data-model).

    별도 시퀀스 테이블을 두지 않는다 — 프로젝트 행 자체가 카운터다.
    """
    from sqlalchemy import update

    stmt = (
        update(Project)
        .where(Project.id == project_id)
        .values(issue_counter=Project.issue_counter + 1)
        .returning(Project.issue_counter)
    )
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        raise LookupError(f"프로젝트를 찾을 수 없다: {project_id}")
    return int(result)


# ── 엔티티 링크 ─────────────────────────────────────────────────
#
# 위키↔이슈처럼 서로 알아야 하는 관계를 중립 테이블로 뺀다. 어느 쪽도 상대
# 모듈을 import 하지 않는다 (overview.md 모듈 의존 그래프).

#: 링크 양 끝의 종류.
ISSUE = "issue"
PAGE = "page"

#: 링크의 뜻. 본문에서 뽑은 참조는 `mentions` 다.
MENTIONS = "mentions"


@dataclass(frozen=True, slots=True)
class LinkRow:
    from_type: str
    from_id: UUID
    to_type: str
    to_id: UUID
    kind: str


def _row(link: EntityLink) -> LinkRow:
    return LinkRow(
        from_type=link.from_type,
        from_id=link.from_id,
        to_type=link.to_type,
        to_id=link.to_id,
        kind=link.kind,
    )


async def replace_links(
    session: AsyncSession,
    *,
    from_type: str,
    from_id: UUID,
    kind: str,
    targets: Sequence[tuple[str, UUID]],
) -> None:
    """한 출처의 링크를 통째로 바꾼다.

    지우고 다시 넣는다. 무엇이 사라졌는지 따로 계산하면, 본문에서 링크를
    지웠을 때 남는 유령 링크를 반드시 한 번은 놓친다.
    """
    await session.execute(
        delete(EntityLink).where(
            EntityLink.from_type == from_type,
            EntityLink.from_id == from_id,
            EntityLink.kind == kind,
        )
    )
    seen: set[tuple[str, UUID]] = set()
    for to_type, to_id in targets:
        if (to_type, to_id) in seen:
            continue
        seen.add((to_type, to_id))
        session.add(
            EntityLink(
                from_type=from_type, from_id=from_id, to_type=to_type, to_id=to_id, kind=kind
            )
        )


async def links_to(
    session: AsyncSession, *, to_type: str, to_id: UUID, from_type: str | None = None
) -> list[LinkRow]:
    """이 엔티티를 가리키는 링크. "이 이슈를 언급한 문서" 가 이걸 쓴다."""
    stmt = select(EntityLink).where(EntityLink.to_type == to_type, EntityLink.to_id == to_id)
    if from_type is not None:
        stmt = stmt.where(EntityLink.from_type == from_type)
    return [_row(r) for r in (await session.execute(stmt)).scalars().all()]


async def links_from(
    session: AsyncSession, *, from_type: str, from_id: UUID, to_type: str | None = None
) -> list[LinkRow]:
    stmt = select(EntityLink).where(
        EntityLink.from_type == from_type, EntityLink.from_id == from_id
    )
    if to_type is not None:
        stmt = stmt.where(EntityLink.to_type == to_type)
    return [_row(r) for r in (await session.execute(stmt)).scalars().all()]


async def drop_links(session: AsyncSession, *, entity_type: str, entity_id: UUID) -> None:
    """엔티티가 사라질 때 양쪽 방향을 모두 지운다."""
    await session.execute(
        delete(EntityLink).where(
            or_(
                and_(EntityLink.from_type == entity_type, EntityLink.from_id == entity_id),
                and_(EntityLink.to_type == entity_type, EntityLink.to_id == entity_id),
            )
        )
    )
