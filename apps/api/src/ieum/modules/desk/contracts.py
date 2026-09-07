"""desk 의 공개 인터페이스. 다른 모듈은 이 파일만 import 한다.

지금은 좁다. `identity` 가 고객 계정을 만들 때 "이 주소면 어느 조직인가" 를
물어야 하고, 그것 하나가 지금 필요한 전부다 — 처음엔 비어 있는 것이 정상이고
(module-guide 3항) 쓰임이 생길 때 넓힌다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.desk.repository import (
    CustomerMembershipRepository,
    CustomerOrganizationRepository,
)


async def organization_for_email(session: AsyncSession, email: str) -> UUID | None:
    """이 주소의 도메인을 주장하는 고객 조직. 없으면 None.

    **가입 시 기본 소속을 정하는 데만 쓴다.** 가시성의 근거가 아니다 —
    근거는 `customer_membership` 행이다. 읽기 시점에 도메인으로 계산하면
    관리자가 `domains` 를 고치는 순간 누가 어느 티켓을 보는지가 조용히
    바뀐다.
    """
    domain = email.strip().lower().rpartition("@")[2]
    if not domain:
        return None
    row = await CustomerOrganizationRepository(session).find_by_domain(domain)
    return row.id if row else None


async def assign_organization(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID
) -> None:
    """고객을 조직에 넣는다. 가입 직후 자동 배정이 쓴다."""
    await CustomerMembershipRepository(session).set(
        user_id=user_id, organization_id=organization_id
    )


async def organization_of(session: AsyncSession, user_id: UUID) -> UUID | None:
    """이 고객의 소속 조직."""
    return await CustomerMembershipRepository(session).organization_of(user_id)
