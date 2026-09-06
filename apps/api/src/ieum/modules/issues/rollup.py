"""부모 이슈 진행률 롤업.

부모의 진행률은 **자식에서 계산한다**. 부모에 직접 적어 두면 자식이 바뀔
때마다 두 값이 어긋나고, 어느 쪽이 진실인지 알 수 없게 된다.

가중치 규칙:
- 자식이 **전부** 추정을 갖고 있으면 추정 시간으로 가중 평균한다.
- 하나라도 없으면 전부 같은 무게로 센다. 없는 추정을 0 으로 치면 추정을
  안 적은 자식이 계산에서 사라진다 — 90% 라고 표시되는데 실제로는 절반이
  손도 안 댄 상태가 된다.

아카이브된 자식은 빼고 센다. 취소한 일 때문에 진행률이 영원히 100% 가
안 되면 숫자를 아무도 안 믿는다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.issues.models import Issue

#: 롤업이 거슬러 올라가는 최대 깊이. 부모-자식은 3단까지지만, 데이터가
#: 망가져 순환이 생겨도 여기서 멈춘다.
MAX_DEPTH = 10


def weighted_progress(children: list[Issue]) -> int:
    """자식들의 진행률을 하나로 합친다. 자식이 없으면 호출하지 않는다."""
    estimates = [c.estimate_minutes for c in children]
    if all(e is not None and e > 0 for e in estimates):
        total = sum(e for e in estimates if e is not None)
        weighted = sum((c.progress * (c.estimate_minutes or 0)) for c in children)
        return round(weighted / total)
    return round(sum(c.progress for c in children) / len(children))


async def has_children(session: AsyncSession, issue_id: UUID) -> bool:
    stmt = (
        select(Issue.id)
        .where(Issue.parent_id == issue_id)
        .where(Issue.archived_at.is_(None))
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def recompute_ancestors(session: AsyncSession, issue_id: UUID) -> list[UUID]:
    """이 이슈의 조상들 진행률을 다시 계산한다.

    바뀐 조상 id 를 돌려준다. 호출자는 트랜잭션 안에서 부르기만 하면 된다 —
    바뀐 게 없으면 아무것도 쓰지 않는다.
    """
    changed: list[UUID] = []
    current = await session.get(Issue, issue_id)
    parent_id = current.parent_id if current else None

    seen: set[UUID] = set()
    depth = 0
    while parent_id is not None and depth < MAX_DEPTH:
        if parent_id in seen:
            # 데이터가 망가져 순환이 생겼다. 무한 루프 대신 멈춘다.
            break
        seen.add(parent_id)
        depth += 1

        parent = await session.get(Issue, parent_id)
        if parent is None:
            break

        children = list(
            (
                await session.execute(
                    select(Issue)
                    .where(Issue.parent_id == parent.id)
                    .where(Issue.archived_at.is_(None))
                )
            ).scalars()
        )
        if children:
            value = weighted_progress(children)
            if parent.progress != value:
                parent.progress = value
                changed.append(parent.id)
        parent_id = parent.parent_id

    if changed:
        await session.flush()
    return changed


__all__ = ["MAX_DEPTH", "has_children", "recompute_ancestors", "weighted_progress"]
