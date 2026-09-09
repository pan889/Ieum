"""identity 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다.

반환 타입은 ORM 모델이 아니라 DTO 다 (module-guide 모듈 간 통신 1번).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.context import Actor
from ieum.core.pagination import PageRequest
from ieum.modules.identity import audit as _audit
from ieum.modules.identity.models import User
from ieum.modules.identity.repository import (
    AuditRepository,
    GroupRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class UserRef:
    """다른 모듈이 사용자를 참조할 때 쓰는 최소 정보."""

    id: UUID
    email: str
    display_name: str
    is_active: bool
    is_customer: bool
    locale: str
    timezone: str


def _to_ref(user: User) -> UserRef:
    return UserRef(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_customer=user.is_customer,
        locale=user.locale,
        timezone=user.timezone,
    )


@dataclass(frozen=True, slots=True)
class GroupRef:
    """다른 모듈이 그룹을 참조할 때 쓰는 최소 정보.

    `role_assignment.principal_id` 는 사람일 수도 그룹일 수도 있다. org 가
    그 행을 사람이 읽을 이름으로 바꾸려면 그룹 이름이 필요한데, `user_group`
    테이블은 identity 의 것이다 — 이 자리가 그 통로다.
    """

    id: UUID
    name: str
    #: `idp` 면 IdP 가 관리한다(auth.md 2절). 화면이 편집 손잡이를 감추는 근거.
    source: str


async def get_group(session: AsyncSession, group_id: UUID) -> GroupRef | None:
    group = await GroupRepository(session).get(group_id)
    return GroupRef(id=group.id, name=group.name, source=group.source) if group else None


async def get_user(session: AsyncSession, user_id: UUID) -> UserRef | None:
    user = await UserRepository(session).get(user_id)
    return _to_ref(user) if user else None


async def get_users(session: AsyncSession, user_ids: Iterable[UUID]) -> dict[UUID, UserRef]:
    """여러 사용자를 한 번에. 참조 검증과 목록 렌더가 행마다 조회하지 않게 한다."""
    unique = list(dict.fromkeys(user_ids))
    if not unique:
        return {}
    rows = await UserRepository(session).get_many(unique)
    return {row.id: _to_ref(row) for row in rows}


async def search_users(
    session: AsyncSession, *, query: str | None = None, limit: int = 20
) -> list[UserRef]:
    """이름·메일로 사용자를 찾는다. 담당자 값을 제안할 때 쓴다.

    사용자 값은 UUID 로 컴파일된다 — 사람이 손으로 칠 수 있는 값이 아니라서,
    이름으로 골라 ID 를 끼워 넣는 길이 없으면 담당자 조건 자체를 못 쓴다.
    """
    page = await UserRepository(session).list_page(PageRequest(limit=limit), query=query)
    return [_to_ref(row) for row in page.items]


async def get_user_by_email(session: AsyncSession, email: str) -> UserRef | None:
    user = await UserRepository(session).get_by_email(email)
    return _to_ref(user) if user else None


async def group_ids_for(session: AsyncSession, user_id: UUID) -> frozenset[UUID]:
    """권한 평가에 필요한 소속 그룹. org 의 리졸버가 사용한다."""
    return await UserRepository(session).group_ids_for(user_id)


async def active_group_members(session: AsyncSession, group_ids: Iterable[UUID]) -> frozenset[UUID]:
    """이 그룹들의 **활성** 구성원. 데스크의 승인 명단(C12)이 쓴다.

    사람 목록을 내주지 않고 id 만 주는 이유: 부르는 쪽이 필요한 것은 "누가
    승인할 수 있나" 이고, 이름은 화면이 따로 묻는다(`get_users`). 사람 행을
    통째로 넘기면 경계가 이름만 남는다.
    """
    return frozenset(await GroupRepository(session).active_member_ids(list(group_ids)))


async def load_actor(session: AsyncSession, user_id: UUID) -> Actor | None:
    """요청 컨텍스트용 액터를 만든다."""
    repo = UserRepository(session)
    user = await repo.get(user_id)
    if user is None:
        return None
    return Actor(
        user_id=user.id,
        email=user.email,
        is_customer=user.is_customer,
        is_active=user.is_active,
        locale=user.locale,
        timezone=user.timezone,
        group_ids=await repo.group_ids_for(user.id),
    )


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """감사 로그 한 줄. 다른 모듈은 이 길로만 남긴다.

    `audit_log` 테이블은 identity 것이다. 남기는 쪽마다 저장소를 직접 열면
    모듈 경계가 무너지고, 무엇을 기록하는지도 흩어진다. 행동 이름은
    `identity.audit` 의 상수를 쓴다 — 조회 화면의 필터 목록이 거기서 온다.
    """
    AuditRepository(session).record(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )


#: 다른 모듈이 남기는 감사 행동. 이름의 정본은 `identity.audit` 이고, 조회
#: 화면의 필터 목록도 거기서 나온다 — 여기서 새로 지어내면 목록에서 빠진다.
AUDIT_SECURITY_MFA_POLICY_CHANGED = _audit.SECURITY_MFA_POLICY_CHANGED


async def invite_customer(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    display_name: str,
    invited_by: UUID,
) -> UserRef:
    """고객 계정을 초대한다. `desk` 가 고객 조직 화면에서 쓴다.

    **쓰는 계약**이다. 지금까지 이 파일은 읽기만 했는데, 고객 계정을 만드는
    길이 필요하고 `user` 테이블은 identity 것이다 — desk 가 그 테이블을 직접
    건드리면 모듈 경계가 무너진다.

    `is_customer=True` 를 여기서 못 바꾸게 고정한 것이 요점이다: 이 통로로
    만든 계정은 언제나 고객이다. desk 가 불린 값을 넘길 수 있게 두면, 다음
    사람이 이걸로 내부 계정도 만들게 된다.
    """
    from ieum.modules.identity.service import UserService

    user = await UserService(session, settings).invite(
        email=email,
        display_name=display_name,
        invited_by=invited_by,
        is_customer=True,
    )
    return _to_ref(user)
