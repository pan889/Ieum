"""search 의 공개 인터페이스.

issues·wiki 가 자기 것을 저장할 때 여기로 색인을 넘긴다. **같은 트랜잭션**
이므로 방금 저장한 것이 곧바로 검색된다 (ADR-0005).

거꾸로 search 는 다른 모듈을 부르지 않는다. 권한 판단에 필요한 것을 색인이
직접 들고 있기 때문이다(스코프와 `restricted_to`). 부르게 두면 두 모듈이
서로를 가리키는 고리가 생긴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.modules.search.repository import SearchRepository

ISSUE = "issue"
PAGE = "page"


async def index_document(
    session: AsyncSession,
    *,
    kind: str,
    entity_id: UUID,
    scope_kind: str,
    scope_id: UUID,
    ref: str,
    title: str,
    body: str,
    restricted_to: Sequence[UUID] | None,
    updated_at: datetime,
) -> None:
    """색인 한 줄을 넣거나 갱신한다.

    `restricted_to` 는 객체 수준 제한을 통과할 수 있는 주체다. None 이면
    제한 없음. 스코프만으로 거르면 보안 레벨 이슈와 제한된 문서가 샌다.
    """
    await SearchRepository(session).upsert(
        {
            # id 는 컬럼 기본값(UUIDv7)이 채운다. 여기서 넣지 않는다.
            "kind": kind,
            "entity_id": entity_id,
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "ref": ref[:500],
            "title": title,
            "body": body,
            "restricted_to": list(restricted_to) if restricted_to is not None else None,
            "source_updated_at": updated_at,
        }
    )


async def remove_document(session: AsyncSession, *, kind: str, entity_id: UUID) -> None:
    await SearchRepository(session).remove(kind, entity_id)


async def remove_documents(session: AsyncSession, *, kind: str, entity_ids: Sequence[UUID]) -> None:
    await SearchRepository(session).remove_many(kind, entity_ids)


__all__ = ["ISSUE", "PAGE", "index_document", "remove_document", "remove_documents"]
