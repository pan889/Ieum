"""첨부 라우터.

서버는 파일 바이트를 경유하지 않는다. 업로드도 다운로드도 presigned URL 을
내주고 클라이언트가 스토리지와 직접 주고받는다.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.attachments import MAX_SIZE_BYTES, AttachmentService
from ieum.core.deps import CurrentActor, DbSession, StorageDep
from ieum.core.storage import DOWNLOAD_TTL_SECONDS

attachments_router = APIRouter(prefix="/attachments", tags=["attachments"])


class UploadBeginRequest(BaseModel):
    owner_type: str = Field(max_length=32)
    owner_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=3, max_length=128)
    size: int = Field(gt=0, le=MAX_SIZE_BYTES)


class UploadTicketResponse(BaseModel):
    attachment_id: UUID
    upload_url: str
    #: PUT 에 그대로 실어야 한다. 서명에 들어 있어 다르면 스토리지가 거절한다.
    headers: dict[str, str]
    expires_in: int


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_type: str
    owner_id: UUID
    filename: str
    mime: str
    size: int
    uploaded_by: UUID | None
    created_at: str | None = None


@attachments_router.post("/upload-url", response_model=UploadTicketResponse)
async def begin_upload(
    body: UploadBeginRequest,
    actor: CurrentActor,
    session: DbSession,
    store: StorageDep,
) -> UploadTicketResponse:
    ticket = await AttachmentService(session, store).begin(
        actor,
        owner_type=body.owner_type,
        owner_id=body.owner_id,
        filename=body.filename,
        mime=body.mime,
        size=body.size,
    )
    await session.commit()
    return UploadTicketResponse(
        attachment_id=ticket.attachment_id,
        upload_url=ticket.upload_url,
        headers=ticket.headers,
        expires_in=ticket.expires_in,
    )


@attachments_router.post("/{attachment_id}/complete", response_model=AttachmentResponse)
async def complete_upload(
    attachment_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    store: StorageDep,
) -> AttachmentResponse:
    row = await AttachmentService(session, store).complete(actor, attachment_id)
    await session.commit()
    return AttachmentResponse(
        id=row.id,
        owner_type=row.owner_type,
        owner_id=row.owner_id,
        filename=row.filename,
        mime=row.mime,
        size=row.size,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at.isoformat(),
    )


@attachments_router.get("", response_model=list[AttachmentResponse])
async def list_attachments(
    owner_type: str,
    owner_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    store: StorageDep,
) -> list[AttachmentResponse]:
    rows = await AttachmentService(session, store).list_for(actor, owner_type, owner_id)
    return [
        AttachmentResponse(
            id=r.id,
            owner_type=r.owner_type,
            owner_id=r.owner_id,
            filename=r.filename,
            mime=r.mime,
            size=r.size,
            uploaded_by=r.uploaded_by,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


class DownloadUrlResponse(BaseModel):
    url: str
    expires_in: int


@attachments_router.get("/{attachment_id}/download-url", response_model=DownloadUrlResponse)
async def download_url(
    attachment_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    store: StorageDep,
) -> DownloadUrlResponse:
    """presigned URL 을 JSON 으로 준다.

    302 로 내보내면 `<a href>` 하나로 끝나 편하지만, 액세스 토큰이 메모리에만
    있고 쿠키가 아니라서(auth.md) 앵커 클릭에는 Authorization 헤더가 안 붙는다.
    그래서 클릭 시점에 이 경로를 인증된 요청으로 부르고, 받은 URL 을 바로 연다.
    URL 은 짧게 만료되므로 저장해 두면 안 된다.
    """
    url = await AttachmentService(session, store).download_url(actor, attachment_id)
    return DownloadUrlResponse(url=url, expires_in=DOWNLOAD_TTL_SECONDS)


@attachments_router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    store: StorageDep,
) -> None:
    await AttachmentService(session, store).delete(actor, attachment_id)
    await session.commit()


__all__ = ["attachments_router"]
