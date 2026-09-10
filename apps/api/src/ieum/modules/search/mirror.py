"""Postgres 색인을 OpenSearch 로 비춘다 (ADR-0015).

## 방향이 한쪽이다

`search_document` 가 정본이고 OpenSearch 는 그것의 사본이다. 그래서 이 파일에
"OpenSearch 에서 읽어 Postgres 에 쓴다" 는 것이 없다. 사본이 어긋나면 다시
만들면 되고(`ieum reindex`), 정본이 어긋나면 그건 다른 문제다.

## 무엇을 큐에 넣는가

**키만** 넣는다. 무엇이 바뀌었는지는 적지 않는다 — 보낼 때 `search_document`
를 다시 읽어 지금 상태를 보내고, 행이 없으면 지운다. 그래서:

- 순서를 안 따진다. 늦게 처리된 오래된 키도 지금 상태를 보낸다.
- 두 번 처리해도 같다.
- 지움이 특별하지 않다. "행이 없다" 가 곧 "지워라" 다.

## OpenSearch 가 죽어 있으면

큐에 남는다. 보내고 **성공한 것만** 지운다. 그래서 살아나면 밀린 것이 따라
붙고, 그동안 검색은 마지막으로 성공한 상태를 보여 준다. 사람은 새 문서가 안
보이는 것으로 그것을 만나므로, 밀린 나이를 `/metrics` 에 낸다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.logging import get_logger
from ieum.core.time import utcnow
from ieum.modules.search.backends.opensearch import OpenSearchBackend
from ieum.modules.search.models import SearchDocument, SearchMirrorQueue

log = get_logger(__name__)

#: 한 번에 보내는 문서 수. bulk 한 통의 크기를 정한다 — 본문이 길 수 있어
#: 크게 잡지 않는다.
BATCH = 100

Key = tuple[str, UUID]


async def enqueue(session: AsyncSession, *, kind: str, entity_ids: Sequence[UUID]) -> None:
    """색인이 바뀐 키를 큐에 넣는다. **같은 트랜잭션**이다.

    커밋되지 않은 변경을 미러가 먼저 보내면 안 되고, 커밋된 변경이 큐에
    안 들어가서도 안 된다. 둘 다 같은 트랜잭션에 있으면 구조적으로 없다.

    `ON CONFLICT DO NOTHING` — 이미 대기 중이면 그대로 둔다. `queued_at` 을
    갱신하면 계속 고쳐지는 문서가 큐 뒤로 밀려 영원히 안 보내진다.
    """
    if not entity_ids:
        return
    now = utcnow()
    stmt = insert(SearchMirrorQueue).values(
        [{"kind": kind, "entity_id": one, "queued_at": now} for one in dict.fromkeys(entity_ids)]
    )
    await session.execute(stmt.on_conflict_do_nothing())


async def backlog(session: AsyncSession) -> tuple[int, float]:
    """대기 중인 키 수와, 그중 가장 오래된 것의 나이(초).

    `outbox_backlog` 와 같은 모양이다 — 경보를 같은 말로 쓸 수 있게.
    """
    row = (
        await session.execute(
            select(func.count(), func.min(SearchMirrorQueue.queued_at)).select_from(
                SearchMirrorQueue
            )
        )
    ).one()
    oldest = row[1]
    age = 0.0 if oldest is None else max(0.0, (utcnow() - oldest).total_seconds())
    return int(row[0] or 0), age


async def flush(session: AsyncSession, settings: Settings, *, limit: int = BATCH) -> int:
    """대기 중인 것을 한 배치 보낸다. 처리한 키 수를 돌려준다.

    `SKIP LOCKED` 로 잠근다 — 워커를 여러 개 띄워도 같은 키를 두 번 보내지
    않는다. 두 번 보내도 결과는 같지만(멱등), 두 번 보내는 것은 낭비다.
    """
    keys: list[Key] = [
        (kind, entity_id)
        for kind, entity_id in (
            await session.execute(
                select(SearchMirrorQueue.kind, SearchMirrorQueue.entity_id)
                .order_by(SearchMirrorQueue.queued_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    ]
    if not keys:
        return 0

    rows = list(
        (await session.execute(select(SearchDocument).where(_documents(keys)))).scalars().all()
    )
    alive = {(row.kind, row.entity_id) for row in rows}
    gone = [key for key in keys if key not in alive]

    backend = OpenSearchBackend(settings)
    try:
        await backend.ensure_index()
        await backend.put([payload(row) for row in rows])
        await backend.drop(gone)
    finally:
        await backend.aclose()

    # **보낸 뒤에 지운다.** 먼저 지우면 보내기가 실패한 키가 사라지고, 그
    # 문서는 다시 고쳐질 때까지 영원히 사본에 없다.
    await session.execute(delete(SearchMirrorQueue).where(_queued(keys)))
    log.info("search.mirror.flushed", sent=len(rows), dropped=len(gone))
    return len(keys)


def _queued(keys: Sequence[Key]) -> ColumnElement[bool]:
    """큐 표에서 이 키들. 복합 PK 라 `tuple_().in_()` 로 한 번에 짚는다."""
    return tuple_(SearchMirrorQueue.kind, SearchMirrorQueue.entity_id).in_(list(keys))


def _documents(keys: Sequence[Key]) -> ColumnElement[bool]:
    """색인 표에서 이 키들. **위와 다른 표다** — 열을 같이 쓰면 안 된다."""
    return tuple_(SearchDocument.kind, SearchDocument.entity_id).in_(list(keys))


def payload(row: SearchDocument) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "entity_id": row.entity_id,
        "scope_kind": row.scope_kind,
        "scope_id": row.scope_id,
        "ref": row.ref,
        "title": row.title,
        "body": row.body,
        "restricted_to": row.restricted_to,
        "source_updated_at": row.source_updated_at.isoformat(),
    }


__all__ = ["BATCH", "Key", "backlog", "enqueue", "flush", "payload"]
