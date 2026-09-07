"""org 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.pagination import PageRequest
from ieum.core.permissions import Acl
from ieum.modules.org.models import EntityLink, Project, Role, RoleAssignment
from ieum.modules.org.repository import ProjectRepository, WorkspaceRepository


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


# ── MFA 강제 정책 ───────────────────────────────────────────────
#
# 정책은 org 가 소유한다(조직 설정과 역할). identity 가 이 두 테이블을 직접
# 보면 모듈 경계를 넘는다 — 그래서 여기서 하나의 판정으로 내준다.

#: 조직 전체 강제 스위치가 사는 자리. `Workspace.settings` 는 JSONB 라
#: 컬럼을 늘리지 않고도 설정을 담을 수 있다.
MFA_SETTING = "require_mfa"


async def workspace_requires_mfa(session: AsyncSession) -> bool:
    """조직 전체 강제 스위치. 설치가 아직 없으면 강제하지 않는다."""
    workspace = await WorkspaceRepository(session).get_single()
    return bool(workspace and workspace.settings.get(MFA_SETTING) is True)


async def set_workspace_requires_mfa(session: AsyncSession, *, required: bool) -> bool:
    """스위치를 바꾸고 새 값을 돌려준다. 설치가 없으면 아무것도 안 한다."""
    workspace = await WorkspaceRepository(session).get_single()
    if workspace is None:
        return False
    # JSONB 는 통째로 갈아 끼워야 변경으로 잡힌다. 키만 바꾸면 SQLAlchemy 가
    # dict 를 같은 객체로 보고 UPDATE 를 내지 않는다.
    workspace.settings = {**workspace.settings, MFA_SETTING: required}
    await session.flush()
    return required


async def mfa_required_for(
    session: AsyncSession, *, user_id: UUID, group_ids: Iterable[UUID] = ()
) -> bool:
    """이 사람이 2FA 를 **반드시** 켜야 하는가.

    조직 전체 스위치가 켜져 있거나, 받은 역할 중 하나라도 요구하면 참이다.
    사용자 개인 플래그(`user.require_mfa`)는 identity 가 따로 본다 — 그건
    org 의 것이 아니다.

    그룹까지 보는 이유는 역할이 그룹에도 붙기 때문이다. 그룹으로 관리자
    역할을 받은 사람이 정책을 비껴가면 정책이 아니다.
    """
    if await workspace_requires_mfa(session):
        return True

    principals: list[tuple[str, UUID]] = [("user", user_id)]
    principals.extend(("group", gid) for gid in group_ids)
    stmt = (
        select(Role.id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(Role.require_mfa.is_(True))
        .where(
            or_(
                *[
                    and_(
                        RoleAssignment.principal_kind == kind,
                        RoleAssignment.principal_id == pid,
                    )
                    for kind, pid in principals
                ]
            )
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None
