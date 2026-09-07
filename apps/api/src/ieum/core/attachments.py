"""첨부. 여러 모듈(이슈·코멘트·위키)이 같은 테이블을 쓴다.

`outbox_event` 와 같은 이유로 core 에 둔다 — 어느 한 모듈의 소유물이 아니고,
모듈끼리 서로를 import 하게 만들면 안 된다.

권한은 core 가 모른다. 소유자 종류별로 **모듈이 리졸버를 등록**하고 core 는
그걸 물어본다 (PermissionService.register_guard 와 같은 방식).

업로드는 두 단계다:
  1. `begin` — 검증 → pending 행 + presigned PUT URL
  2. 클라이언트가 스토리지로 직접 PUT
  3. `complete` — 실제로 올라왔는지 HEAD 로 확인하고 ready 로 바꾼다
2단계가 없으면 "행은 있는데 파일은 없는" 첨부가 목록에 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.logging import get_logger
from ieum.core.storage import UPLOAD_TTL_SECONDS, ObjectStore, safe_filename
from ieum.core.time import utcnow
from ieum.db.base import Entity

log = get_logger(__name__)

#: 한 파일 최대 크기. 설치마다 다를 수 있지만 기본은 보수적으로 잡는다.
MAX_SIZE_BYTES = 25 * 1024 * 1024

#: 인라인으로 열어도 되는 타입. 나머지는 Content-Disposition: attachment 다.
#: SVG 는 뺀다 — 스크립트를 품을 수 있어서 이미지지만 인라인이면 XSS 다.
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"})

#: 아예 받지 않는 타입. 브라우저가 우리 오리진에서 실행할 수 있는 것들.
BLOCKED_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "application/x-msdownload",
        "application/x-sh",
    }
)

STATUS_PENDING = "pending"
STATUS_READY = "ready"


class Attachment(Entity):
    __tablename__ = "attachment"

    #: issue / comment / page. 소유자 모듈이 리졸버를 등록한다.
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    #: pending → ready. pending 은 아직 파일이 올라오지 않은 상태다.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("ix_attachment_owner", "owner_type", "owner_id"),
        Index("ix_attachment_status", "status"),
    )


@dataclass(frozen=True, slots=True)
class UploadTicket:
    attachment_id: UUID
    upload_url: str
    #: 클라이언트가 PUT 에 그대로 실어야 한다. 서명에 들어 있어 다르면 거절된다.
    headers: dict[str, str]
    expires_in: int


class OwnerResolver(Protocol):
    """소유자에 대한 권한 판정. 모듈이 등록한다."""

    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool: ...

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool: ...


_resolvers: dict[str, OwnerResolver] = {}


def register_owner(owner_type: str, resolver: OwnerResolver) -> None:
    _resolvers[owner_type] = resolver


def _resolver_for(owner_type: str) -> OwnerResolver:
    resolver = _resolvers.get(owner_type)
    if resolver is None:
        # 등록 안 된 소유자 종류는 거절한다. 열어 두면 권한 검사 없이
        # 아무 UUID 에나 파일을 붙일 수 있다.
        raise ValidationError(
            "지원하지 않는 첨부 대상이다.",
            code="attachments.unknown_owner",
            details={"owner_type": owner_type},
        )
    return resolver


class AttachmentService:
    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self._s = session
        self._store = store

    async def begin(
        self,
        actor: Actor,
        *,
        owner_type: str,
        owner_id: UUID,
        filename: str,
        mime: str,
        size: int,
    ) -> UploadTicket:
        resolver = _resolver_for(owner_type)
        if not await resolver.can_attach(self._s, actor, owner_id):
            raise PermissionDeniedError("이 대상에 파일을 붙일 권한이 없다.")

        normalized = _validate(filename, mime, size)
        attachment_id = new_id()
        # 키는 **서버가 정한다**. 클라이언트가 정하면 남의 첨부를 덮어쓸 수 있다.
        key = _storage_key(attachment_id, filename)

        self._s.add(
            Attachment(
                id=attachment_id,
                owner_type=owner_type,
                owner_id=owner_id,
                filename=safe_filename(filename),
                mime=normalized,
                size=size,
                storage_key=key,
                status=STATUS_PENDING,
                uploaded_by=actor.user_id,
            )
        )
        await self._s.flush()

        return UploadTicket(
            attachment_id=attachment_id,
            upload_url=self._store.upload_url(key, content_type=normalized, content_length=size),
            headers={"Content-Type": normalized},
            expires_in=UPLOAD_TTL_SECONDS,
        )

    async def complete(self, actor: Actor, attachment_id: UUID) -> Attachment:
        """올라온 걸 확인하고 ready 로 바꾼다.

        HEAD 로 실제 크기를 본다. presigned PUT 은 서명에 길이를 박아 두지만
        스토리지 구현에 따라 느슨할 수 있어 여기서 한 번 더 본다.
        """
        row = await self._require(attachment_id)
        resolver = _resolver_for(row.owner_type)
        if not await resolver.can_attach(self._s, actor, row.owner_id):
            raise PermissionDeniedError("이 첨부를 확정할 권한이 없다.")
        if row.status == STATUS_READY:
            return row

        info = await self._store.head(row.storage_key)
        if info is None:
            raise ConflictError("업로드가 확인되지 않았다.", code="attachments.upload_missing")
        if info.size != row.size:
            # 약속과 다른 크기가 올라왔다. 지우고 거절한다 — 남겨 두면
            # 상한을 우회한 파일이 스토리지에 그대로 쌓인다.
            await self._store.delete(row.storage_key)
            await self._s.delete(row)
            await self._s.flush()
            raise ValidationError(
                "업로드된 크기가 신고한 크기와 다르다.",
                code="attachments.size_mismatch",
                details={"declared": row.size, "actual": info.size},
            )

        row.status = STATUS_READY
        await self._s.flush()
        log.info("attachment.ready", attachment=str(row.id), size=row.size)
        return row

    async def ingest(
        self,
        actor: Actor,
        *,
        owner_type: str,
        owner_id: UUID,
        filename: str,
        mime: str,
        data: bytes,
    ) -> Attachment:
        """서버가 손에 쥔 바이트를 첨부로. 바로 `ready` 다.

        presigned 왕복(begin → 브라우저 PUT → complete)은 **브라우저**를 위한
        절차다. ZIP 안의 그림처럼 서버가 이미 읽은 파일에는 그 세 걸음이
        의미가 없고, 중간에 끊기면 pending 행만 남는다.
        """
        resolver = _resolver_for(owner_type)
        if not await resolver.can_attach(self._s, actor, owner_id):
            raise PermissionDeniedError("이 대상에 파일을 붙일 권한이 없다.")

        normalized = _validate(filename, mime, len(data))
        attachment_id = new_id()
        key = _storage_key(attachment_id, filename)
        await self._store.put(key, data, content_type=normalized)

        row = Attachment(
            id=attachment_id,
            owner_type=owner_type,
            owner_id=owner_id,
            filename=safe_filename(filename),
            mime=normalized,
            size=len(data),
            storage_key=key,
            status=STATUS_READY,
            uploaded_by=actor.user_id,
        )
        self._s.add(row)
        await self._s.flush()
        log.info("attachment.ingested", attachment=str(row.id), size=row.size)
        return row

    async def list_for(self, actor: Actor, owner_type: str, owner_id: UUID) -> list[Attachment]:
        resolver = _resolver_for(owner_type)
        if not await resolver.can_view(self._s, actor, owner_id):
            raise PermissionDeniedError("이 대상을 볼 권한이 없다.")
        stmt = (
            select(Attachment)
            .where(Attachment.owner_type == owner_type)
            .where(Attachment.owner_id == owner_id)
            # pending 은 목록에 넣지 않는다. 아직 파일이 없다.
            .where(Attachment.status == STATUS_READY)
            .order_by(Attachment.created_at)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def download_url(self, actor: Actor, attachment_id: UUID) -> str:
        row = await self._require(attachment_id)
        resolver = _resolver_for(row.owner_type)
        # 존재 자체를 숨긴다. 404 와 403 을 구분해 주면 첨부 id 로 비공개
        # 이슈의 존재를 확인할 수 있다.
        if not await resolver.can_view(self._s, actor, row.owner_id):
            raise NotFoundError("첨부를 찾을 수 없다.")
        if row.status != STATUS_READY:
            raise NotFoundError("첨부를 찾을 수 없다.")
        return self._store.download_url(
            row.storage_key, filename=row.filename, inline=row.mime in INLINE_TYPES
        )

    async def delete(self, actor: Actor, attachment_id: UUID) -> None:
        row = await self._require(attachment_id)
        resolver = _resolver_for(row.owner_type)
        if not await resolver.can_attach(self._s, actor, row.owner_id):
            raise PermissionDeniedError("이 첨부를 지울 권한이 없다.")
        await self._store.delete(row.storage_key)
        await self._s.delete(row)

    async def sweep_pending(self, older_than_seconds: int = 3600) -> int:
        """확정되지 않은 첨부를 치운다. 워커가 주기적으로 부른다.

        업로드 중 창을 닫으면 pending 행만 남는다. 안 치우면 목록에는 안
        보여도 테이블과 스토리지에 영원히 쌓인다.
        """
        cutoff = utcnow().timestamp() - older_than_seconds
        stmt = select(Attachment).where(Attachment.status == STATUS_PENDING)
        rows = list((await self._s.execute(stmt)).scalars())
        removed = 0
        for row in rows:
            if row.created_at.timestamp() > cutoff:
                continue
            await self._store.delete(row.storage_key)
            await self._s.delete(row)
            removed += 1
        if removed:
            log.info("attachment.swept", count=removed)
        return removed

    async def _require(self, attachment_id: UUID) -> Attachment:
        row = await self._s.get(Attachment, attachment_id)
        if row is None:
            raise NotFoundError("첨부를 찾을 수 없다.")
        return row


def _validate(filename: str, mime: str, size: int) -> str:
    """검증하고 정규화된 MIME 을 돌려준다."""
    if not filename.strip():
        raise ValidationError("파일 이름이 없다.", code="attachments.invalid_name")
    # bool 은 int 의 서브클래스다. True 가 1바이트가 되면 안 된다.
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValidationError("파일 크기가 올바르지 않다.", code="attachments.invalid_size")
    if size > MAX_SIZE_BYTES:
        raise ValidationError(
            "파일이 너무 크다.",
            code="attachments.too_large",
            details={"max": MAX_SIZE_BYTES, "size": size},
        )
    normalized = mime.split(";")[0].strip().lower()
    if not normalized or "/" not in normalized:
        raise ValidationError("MIME 타입이 올바르지 않다.", code="attachments.invalid_mime")
    if normalized in BLOCKED_TYPES:
        raise ValidationError(
            "이 형식은 올릴 수 없다.",
            code="attachments.blocked_type",
            details={"mime": normalized},
        )
    return normalized


def _storage_key(attachment_id: UUID, filename: str) -> str:
    now = utcnow()
    return f"att/{now:%Y/%m}/{attachment_id}/{safe_filename(filename)}"


__all__ = [
    "BLOCKED_TYPES",
    "INLINE_TYPES",
    "MAX_SIZE_BYTES",
    "STATUS_PENDING",
    "STATUS_READY",
    "Attachment",
    "AttachmentService",
    "OwnerResolver",
    "UploadTicket",
    "register_owner",
]
