"""저장소 연동 라우터 (A22, M6).

**웹훅 수신은 여기 없다** (`webhook_router.py`). 그쪽은 인증이 다른 길이고 —
액세스 토큰이 아니라 서명으로 확인한다 — 한 파일에 섞으면 인증 없는 경로가
인증 있는 경로들 사이에 숨는다.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.vcs.models import PROVIDERS, ChangeLink, Repository
from ieum.modules.vcs.service import LinkedChange, NewRepository, RepositoryService

repositories_router = APIRouter(prefix="/repositories", tags=["repositories"])


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    name: str
    url: str | None
    project_ids: list[str]
    is_enabled: bool
    #: 마지막으로 뭔가 받은 때. **켰는데 안 오는 것**이 이 연동의 흔한
    #: 고장이고, 화면은 이 값이 비어 있는 것으로만 그것을 말할 수 있다.
    last_event_at: datetime | None

    @classmethod
    def of(cls, row: Repository) -> RepositoryResponse:
        return cls.model_validate(row)


class IssuedRepositoryResponse(RepositoryResponse):
    """등록 결과. **시크릿은 이 응답에만 있다.**"""

    secret: str
    #: 코드 호스트에 넣을 주소. 화면이 경로를 짜지 않게 서버가 준다.
    webhook_path: str


class RepositoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(pattern=f"^({'|'.join(PROVIDERS)})$")
    name: str = Field(min_length=1, max_length=200)
    #: 이 저장소의 커밋이 가리킬 수 있는 프로젝트. **비울 수 없다.**
    project_ids: list[UUID] = Field(min_length=1, max_length=20)
    url: str | None = Field(default=None, max_length=500)

    def to_payload(self) -> NewRepository:
        return NewRepository(
            provider=self.provider,
            name=self.name,
            project_ids=tuple(self.project_ids),
            url=self.url,
        )


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool


class ChangeLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    external_ref: str
    title: str
    url: str
    author: str | None
    closing: bool
    happened_at: datetime
    repository_name: str
    provider: str

    @classmethod
    def of(cls, found: LinkedChange) -> ChangeLinkResponse:
        link: ChangeLink = found.link
        return cls(
            id=link.id,
            kind=link.kind,
            external_ref=link.external_ref,
            title=link.title,
            url=link.url,
            author=link.author,
            closing=link.closing,
            happened_at=link.happened_at,
            repository_name=found.repository_name,
            provider=found.provider,
        )


@repositories_router.get("", response_model=list[RepositoryResponse])
async def list_repositories(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[RepositoryResponse]:
    rows = await RepositoryService(session, permissions).list_for(actor, project_id)
    return [RepositoryResponse.of(row) for row in rows]


@repositories_router.post(
    "", response_model=IssuedRepositoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_repository(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: RepositoryCreateRequest,
) -> IssuedRepositoryResponse:
    """등록한다. **시크릿은 이 응답에만 나온다** — 잃으면 새로 만든다."""
    issued = await RepositoryService(session, permissions).create(actor, payload.to_payload())
    await session.commit()
    base = RepositoryResponse.of(issued.repository)
    return IssuedRepositoryResponse(
        **base.model_dump(),
        secret=issued.secret,
        webhook_path=f"/api/v1/vcs/{issued.repository.provider}/{issued.repository.id}",
    )


@repositories_router.get("/links", response_model=list[ChangeLinkResponse])
async def list_links(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, issue_id: UUID
) -> list[ChangeLinkResponse]:
    """이 이슈에 붙은 커밋·PR.

    **`/{repository_id}` 형제보다 위에 있어야 한다** — 아래로 내려가면
    `links` 가 저장소 id 로 잡혀 UUID 파싱 오류가 난다 (conventions
    "고정 경로는 형제 전부보다 위에 둔다").
    """
    found = await RepositoryService(session, permissions).links_for(actor, issue_id)
    return [ChangeLinkResponse.of(row) for row in found]


@repositories_router.post("/{repository_id}/enabled", response_model=RepositoryResponse)
async def set_repository_enabled(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    repository_id: UUID,
    payload: EnabledRequest,
) -> RepositoryResponse:
    row = await RepositoryService(session, permissions).set_enabled(
        actor, repository_id, enabled=payload.is_enabled
    )
    await session.commit()
    return RepositoryResponse.of(row)


@repositories_router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, repository_id: UUID
) -> None:
    await RepositoryService(session, permissions).delete(actor, repository_id)
    await session.commit()


__all__ = ["repositories_router"]
