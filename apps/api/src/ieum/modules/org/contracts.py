"""org 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.org.models import Project
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


async def get_project_by_key(session: AsyncSession, key: str) -> ProjectRef | None:
    project = await ProjectRepository(session).get_by_key(key)
    return _to_ref(project) if project else None


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
