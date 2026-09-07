"""권한을 붙인 액터를 만드는 도우미.

권한 검사를 지나야 하는 서비스 테스트는 전부 "역할을 만들고 → 권한을
넣고 → 주체에게 할당" 을 반복한다. 한 곳에 둔다 — 테스트 파일마다 복사해
두면 액터의 모양(특히 step-up 조건)이 갈라지고, 갈라진 쪽이 실제보다
느슨해진다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.permissions import Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.org.models import Role
from ieum.modules.org.repository import RoleRepository


def actor_for(user: User, *, group_ids: frozenset[UUID] = frozenset()) -> Actor:
    """step-up 이 필요한 권한도 쓰므로 MFA 를 통과한 액터로 만든다."""
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_satisfied_at=utcnow(),
        # 시각만으로는 부족하다 — MFA 를 등록하지 않은 계정도 로그인하면
        # 그 값이 채워진다. 이 액터는 실제로 통과한 사람이다.
        mfa_verified=True,
        group_ids=group_ids,
    )


async def grant(
    session: AsyncSession,
    *,
    principal_id: UUID,
    permissions_granted: tuple[str, ...],
    scope: Scope,
    principal_kind: str = "user",
    scope_kind: str | None = None,
) -> Role:
    """역할을 만들고 주체에게 할당한다."""
    repo = RoleRepository(session)
    role = Role(name=f"role-{new_id()}", scope_kind=scope_kind or scope.kind.value)
    repo.add(role)
    await session.flush()
    for permission in permissions_granted:
        repo.grant(role.id, permission)
    repo.assign(
        role_id=role.id, scope=scope, principal_kind=principal_kind, principal_id=principal_id
    )
    await session.flush()
    return role


__all__ = ["actor_for", "grant"]
