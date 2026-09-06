"""통합 검색 API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.search.models import DOCUMENT_KINDS
from ieum.modules.search.service import DEFAULT_LIMIT, MAX_LIMIT, SearchService

router = APIRouter(prefix="/search", tags=["search"])


class HitResponse(BaseModel):
    kind: str
    entity_id: UUID
    #: 사람이 읽는 식별자. 이슈 키(`ENG-1`)나 문서 경로(`ENG/deploy`).
    ref: str
    title: str
    snippet: str
    scope_id: UUID
    updated_at: datetime


class SearchResponse(BaseModel):
    items: list[HitResponse]
    total: int
    #: 화면이 굵게 칠할 낱말. 서버는 HTML 을 만들지 않는다.
    keywords: list[str]


@router.get("", response_model=SearchResponse)
async def search_everything(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    q: Annotated[str, Query(max_length=500)] = "",
    kind: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> SearchResponse:
    """이슈와 문서를 한 번에 찾는다.

    권한은 질의에 얹혀 있다 — 볼 수 없는 것은 애초에 결과에 들지 않는다.
    """
    results = await SearchService(session, permissions).search(
        actor,
        query=q,
        kinds=tuple(kind) if kind else DOCUMENT_KINDS,
        limit=limit,
        offset=offset,
    )
    return SearchResponse(
        items=[
            HitResponse(
                kind=hit.kind,
                entity_id=hit.entity_id,
                ref=hit.ref,
                title=hit.title,
                snippet=hit.snippet,
                scope_id=hit.scope_id,
                updated_at=hit.updated_at,
            )
            for hit in results.hits
        ],
        total=results.total,
        keywords=results.keywords,
    )


__all__ = ["router"]
