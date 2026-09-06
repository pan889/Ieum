"""IQL 검색·검증·저장 필터 라우터."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.core.time import utcnow
from ieum.modules.issues.export import stream_csv, validate_export_query
from ieum.modules.issues.router import summary_rows
from ieum.modules.issues.schemas import IssuePageResponse
from ieum.modules.issues.search import (
    SavedFilterService,
    SearchService,
    field_catalog,
    function_catalog,
)
from ieum.modules.issues.service import IssueService

search_router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    iql: str = Field(default="", max_length=4000)
    cursor: str | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class ValidateRequest(BaseModel):
    iql: str = Field(default="", max_length=4000)


class ValidateResponse(BaseModel):
    valid: bool
    error: dict[str, Any] | None = None
    fields: list[str] | None = None


class SavedFilterCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    iql: str = Field(min_length=1, max_length=4000)
    description: str | None = Field(default=None, max_length=1000)
    is_shared: bool = False


class SavedFilterUpdateRequest(BaseModel):
    """부분 수정. 미포함이면 건드리지 않는다."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    iql: str | None = Field(default=None, min_length=1, max_length=4000)
    description: str | None = Field(default=None, max_length=1000)
    is_shared: bool | None = None


class SavedFilterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str | None
    iql: str
    is_shared: bool


@search_router.post("/search/issues", response_model=IssuePageResponse)
async def search_issues(
    body: SearchRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> IssuePageResponse:
    """IQL 검색. 권한 필터는 항상 AND 로 붙는다."""
    page = await SearchService(session, permissions).search(
        actor, body.iql, PageRequest(limit=body.limit, cursor=body.cursor)
    )
    return IssuePageResponse(
        items=await summary_rows(IssueService(session, permissions), page.items),
        next_cursor=page.next_cursor,
    )


@search_router.post("/iql/validate", response_model=ValidateResponse)
async def validate_iql(
    body: ValidateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> ValidateResponse:
    """문법·필드 검증. 200 으로 결과를 담아 준다 — 타이핑 중 호출되므로
    오류를 4xx 로 던지면 에디터가 시끄러워진다."""
    result = await SearchService(session, permissions).validate(actor, body.iql)
    return ValidateResponse(valid=result.valid, error=result.error, fields=result.fields)


@search_router.get("/iql/fields")
async def list_iql_fields(actor: CurrentActor) -> dict[str, Any]:
    """필터 칩과 자동완성이 쓰는 카탈로그."""
    return {"fields": field_catalog(), "functions": function_catalog()}


# ── 저장 필터 ───────────────────────────────────────────────────

filters_router = APIRouter(prefix="/filters", tags=["filters"])


@filters_router.post("", response_model=SavedFilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    body: SavedFilterCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> SavedFilterResponse:
    row = await SavedFilterService(session, permissions).create(
        actor,
        name=body.name,
        iql=body.iql,
        description=body.description,
        is_shared=body.is_shared,
    )
    await session.commit()
    return SavedFilterResponse.model_validate(row)


@filters_router.get("", response_model=list[SavedFilterResponse])
async def list_filters(
    actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[SavedFilterResponse]:
    rows = await SavedFilterService(session, permissions).list_for(actor)
    return [SavedFilterResponse.model_validate(r) for r in rows]


@filters_router.get("/{filter_id}/results", response_model=IssuePageResponse)
async def run_filter(
    filter_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> IssuePageResponse:
    """저장 필터 실행. 소유자가 아니라 **실행자** 권한으로 돈다."""
    page = await SavedFilterService(session, permissions).run(
        actor, filter_id, PageRequest(limit=limit, cursor=cursor)
    )
    return IssuePageResponse(
        items=await summary_rows(IssueService(session, permissions), page.items),
        next_cursor=page.next_cursor,
    )


@filters_router.patch("/{filter_id}", response_model=SavedFilterResponse)
async def update_filter(
    filter_id: UUID,
    body: SavedFilterUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> SavedFilterResponse:
    """질의를 다듬어도 필터 id 는 그대로다 — 공유 링크가 안 끊긴다."""
    row = await SavedFilterService(session, permissions).update(
        actor,
        filter_id,
        name=body.name,
        iql=body.iql,
        description=body.description,
        is_shared=body.is_shared,
    )
    await session.commit()
    return SavedFilterResponse.model_validate(row)


@filters_router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    filter_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await SavedFilterService(session, permissions).delete(actor, filter_id)
    await session.commit()


@search_router.post("/search/issues/export")
async def export_issues(
    body: ValidateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> StreamingResponse:
    """IQL 결과를 CSV 로 흘려보낸다.

    검색(1000건 상한)과 다른 경로다 — 내보내기는 "전부 달라" 는 요청이라
    같은 상한을 쓸 수 없다 (query-language.md 3절).
    """
    validate_export_query(body.iql)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        stream_csv(session, permissions, actor, body.iql),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ieum-issues-{stamp}.csv"',
            # 내보내기 결과는 사용자별 ACL 을 탄다. 중간 캐시에 남으면 안 된다.
            "Cache-Control": "no-store",
        },
    )
