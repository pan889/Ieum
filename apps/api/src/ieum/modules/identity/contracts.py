"""identity 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다.

반환 타입은 ORM 모델이 아니라 DTO 다 (module-guide 모듈 간 통신 1번).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.modules.identity.models import User
from ieum.modules.identity.repository import UserRepository


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


async def get_user_by_email(session: AsyncSession, email: str) -> UserRef | None:
    user = await UserRepository(session).get_by_email(email)
    return _to_ref(user) if user else None


async def group_ids_for(session: AsyncSession, user_id: UUID) -> frozenset[UUID]:
    """권한 평가에 필요한 소속 그룹. org 의 리졸버가 사용한다."""
    return await UserRepository(session).group_ids_for(user_id)


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
