"""첨부. moto 로 진짜 S3 서버를 띄워 presigned 업로드 전체 경로를 돈다.

서명만 만들어 보고 끝내면 "서명은 맞는데 실제로는 거절되는" 조합을 못 잡는다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.attachments import (
    MAX_SIZE_BYTES,
    STATUS_PENDING,
    STATUS_READY,
    Attachment,
    AttachmentService,
    register_owner,
)
from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.storage import ObjectStore, safe_filename
from ieum.core.time import utcnow
from ieum.modules.identity.models import User

pytestmark = pytest.mark.usefixtures("fake_owner")

OWNER_OPEN = "test-open"
OWNER_LOCKED = "test-locked"


class _AllowAll:
    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        return True

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        return True


class _ViewOnly:
    async def can_view(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        return True

    async def can_attach(self, session: AsyncSession, actor: Actor, owner_id: UUID) -> bool:
        return False


@pytest.fixture(scope="session", autouse=True)
def fake_owner() -> None:
    register_owner(OWNER_OPEN, _AllowAll())
    register_owner(OWNER_LOCKED, _ViewOnly())


@pytest.fixture(scope="session")
def s3_server() -> Iterator[str]:
    """in-process S3. 실제 HTTP 를 타므로 presigned URL 을 그대로 쓸 수 있다."""
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    _ENDPOINT[0] = endpoint
    yield endpoint
    server.stop()


@pytest.fixture
def store(s3_server: str, settings: Settings) -> ObjectStore:
    configured = settings.model_copy(
        update={
            "s3_endpoint_url": s3_server,
            "s3_bucket": f"test-{secrets.token_hex(4)}",
            "s3_access_key": SecretStr("test"),
            "s3_secret_key": SecretStr("test"),
        }
    )
    return ObjectStore(configured)


@pytest_asyncio.fixture
async def ready_store(store: ObjectStore) -> AsyncIterator[ObjectStore]:
    await store.ensure_bucket()
    yield store


@pytest_asyncio.fixture
async def uploader(session: AsyncSession) -> AsyncIterator[Actor]:
    """실제 user 행이 있어야 한다. uploaded_by 에 FK 가 걸려 있다."""
    user = User(email=f"up-{new_id()}@example.com", display_name="Uploader", status="active")
    session.add(user)
    await session.flush()
    yield Actor(user_id=user.id, email=user.email, is_active=True, mfa_satisfied_at=utcnow())


def _put_directly(store: ObjectStore, key: str, body: bytes) -> None:
    """서비스를 거치지 않고 버킷에 직접 쓴다 (외부 행위자 흉내)."""
    import boto3
    from botocore.client import Config

    client = boto3.client(
        "s3",
        endpoint_url=_ENDPOINT[0],
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.put_object(Bucket=store.bucket, Key=key, Body=body)


#: s3_server 픽스처가 채운다. _put_directly 가 세션 픽스처를 못 받아서 둔다.
_ENDPOINT: list[str] = [""]


async def _put(url: str, body: bytes, headers: dict[str, str]) -> int:
    async with httpx.AsyncClient() as client:
        response = await client.put(url, content=body, headers=headers)
    return response.status_code


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a.png", "a.png"),
            ("../../etc/passwd", "_.._etc_passwd"),
            ("with space.txt", "with space.txt"),
            ("bad\nname.txt", "bad_name.txt"),
            ("", "file"),
            ("...", "file"),
        ],
    )
    def test_strips_path_and_control_characters(self, raw: str, expected: str) -> None:
        # 개행이 남으면 Content-Disposition 헤더를 쪼갤 수 있다.
        assert safe_filename(raw) == expected


class TestPublicEndpoint:
    """presigned URL 은 **브라우저가** 연다.

    compose 로 띄우면 서버는 `http://minio:9000` 을 보고 브라우저는 못 본다.
    컨테이너 이름으로 서명하면 업로드가 통째로 죽는다 — 실제로 그랬다.
    """

    def _settings(self, internal: str, public: str | None) -> Settings:
        return Settings(
            secret_key=SecretStr("x" * 32),
            s3_endpoint_url=internal,
            s3_public_endpoint_url=public,
            s3_bucket="b",
            s3_access_key=SecretStr("k"),
            s3_secret_key=SecretStr("s"),
        )

    def test_signs_with_the_public_host(self) -> None:
        store = ObjectStore(self._settings("http://minio:9000", "http://localhost:9000"))
        url = store.upload_url("k/x.txt", content_type="text/plain", content_length=3)
        assert urlparse(url).netloc == "localhost:9000"

    def test_download_urls_use_it_too(self) -> None:
        store = ObjectStore(self._settings("http://minio:9000", "http://localhost:9000"))
        url = store.download_url("k/x.txt", filename="x.txt", inline=False)
        assert urlparse(url).netloc == "localhost:9000"

    def test_falls_back_to_the_internal_host(self) -> None:
        # 컨테이너 밖에서 돌 때는 하나뿐이다. 설정 하나를 더 요구하면 안 된다.
        store = ObjectStore(self._settings("http://127.0.0.1:9555", None))
        url = store.upload_url("k/x.txt", content_type="text/plain", content_length=3)
        assert urlparse(url).netloc == "127.0.0.1:9555"


class TestUploadRoundTrip:
    async def test_full_path(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        body = b"hello attachment"
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="note.txt",
            mime="text/plain",
            size=len(body),
        )
        assert await _put(ticket.upload_url, body, ticket.headers) in (200, 204)

        row = await service.complete(uploader, ticket.attachment_id)
        assert row.status == STATUS_READY
        assert row.size == len(body)

    async def test_content_type_and_length_are_in_the_signature(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """타입과 길이를 서명에 넣는다.

        안 넣으면 png 라고 신고하고 HTML 을 올려 우리 오리진에서 실행시킬 수
        있고, 크기 상한도 우회된다. 실제 거절은 스토리지가 한다(moto 는
        서명된 헤더를 강제하지 않아 여기서는 서명 내용만 확인한다 — complete
        의 HEAD 검사가 두 번째 방어선이다).
        """
        ticket = await AttachmentService(session, ready_store).begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="a.png",
            mime="image/png",
            size=10,
        )
        query = dict(parse_qsl(urlparse(ticket.upload_url).query))
        signed = set(query["X-Amz-SignedHeaders"].split(";"))
        assert {"content-type", "content-length"} <= signed
        assert ticket.headers["Content-Type"] == "image/png"

    async def test_complete_without_upload_conflicts(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """행은 있는데 파일은 없는 첨부가 목록에 남으면 안 된다."""
        ticket = await AttachmentService(session, ready_store).begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="ghost.txt",
            mime="text/plain",
            size=5,
        )
        with pytest.raises(ConflictError, match="확인되지 않았다"):
            await AttachmentService(session, ready_store).complete(uploader, ticket.attachment_id)

    async def test_size_mismatch_deletes_the_object(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """상한을 우회한 파일이 스토리지에 남으면 안 된다."""
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="a.bin",
            mime="application/octet-stream",
            size=10,
        )
        # 스토리지에 직접 다른 크기를 넣는다. 서명이 느슨하거나 다른 경로로
        # 객체가 들어온 상황을 흉내 낸다 — complete 가 마지막 방어선이다.
        row = await session.get(Attachment, ticket.attachment_id)
        assert row is not None
        key = row.storage_key
        _put_directly(ready_store, key, b"x" * 999)

        with pytest.raises(ValidationError, match="크기"):
            await service.complete(uploader, ticket.attachment_id)
        assert await ready_store.head(key) is None
        assert await session.get(Attachment, ticket.attachment_id) is None

    async def test_download_url_actually_serves_the_bytes(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        body = b"downloadable"
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="doc.txt",
            mime="text/plain",
            size=len(body),
        )
        await _put(ticket.upload_url, body, ticket.headers)
        await service.complete(uploader, ticket.attachment_id)

        url = await service.download_url(uploader, ticket.attachment_id)
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
        assert response.status_code == 200
        assert response.content == body
        # 텍스트는 인라인으로 열지 않는다.
        assert "attachment" in response.headers.get("content-disposition", "")

    async def test_images_open_inline(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        body = b"\x89PNG\r\n"
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="p.png",
            mime="image/png",
            size=len(body),
        )
        await _put(ticket.upload_url, body, ticket.headers)
        await service.complete(uploader, ticket.attachment_id)
        url = await service.download_url(uploader, ticket.attachment_id)
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
        assert "inline" in response.headers.get("content-disposition", "")


class TestValidation:
    @pytest.mark.parametrize(
        "mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "TEXT/HTML"]
    )
    async def test_blocks_executable_types(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor, mime: str
    ) -> None:
        """브라우저가 우리 오리진에서 실행할 수 있는 것은 아예 안 받는다."""
        with pytest.raises(ValidationError, match="올릴 수 없다"):
            await AttachmentService(session, ready_store).begin(
                uploader,
                owner_type=OWNER_OPEN,
                owner_id=new_id(),
                filename="x",
                mime=mime,
                size=10,
            )

    async def test_rejects_oversized(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="너무 크다"):
            await AttachmentService(session, ready_store).begin(
                uploader,
                owner_type=OWNER_OPEN,
                owner_id=new_id(),
                filename="big.bin",
                mime="application/octet-stream",
                size=MAX_SIZE_BYTES + 1,
            )

    async def test_rejects_unknown_owner_type(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """열어 두면 권한 검사 없이 아무 UUID 에나 파일을 붙일 수 있다."""
        with pytest.raises(ValidationError, match="지원하지 않는"):
            await AttachmentService(session, ready_store).begin(
                uploader,
                owner_type="whatever",
                owner_id=new_id(),
                filename="x.txt",
                mime="text/plain",
                size=10,
            )

    async def test_storage_key_is_server_chosen(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """클라이언트가 키를 정하면 남의 첨부를 덮어쓸 수 있다."""
        ticket = await AttachmentService(session, ready_store).begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="../../evil.txt",
            mime="text/plain",
            size=10,
        )
        row = await session.get(Attachment, ticket.attachment_id)
        assert row is not None
        assert row.storage_key.startswith("att/")
        # 파일명 안의 점은 남아도 된다. 상위로 올라가는 **경로 세그먼트**가
        # 없어야 한다.
        assert not {".", ".."} & set(row.storage_key.split("/"))
        assert str(ticket.attachment_id) in row.storage_key


class TestPermissions:
    async def test_cannot_attach_without_permission(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await AttachmentService(session, ready_store).begin(
                uploader,
                owner_type=OWNER_LOCKED,
                owner_id=new_id(),
                filename="x.txt",
                mime="text/plain",
                size=10,
            )

    async def test_pending_is_hidden_from_the_list(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        owner_id = new_id()
        await AttachmentService(session, ready_store).begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=owner_id,
            filename="pending.txt",
            mime="text/plain",
            size=10,
        )
        rows = await AttachmentService(session, ready_store).list_for(
            uploader, OWNER_OPEN, owner_id
        )
        assert rows == []

    async def test_pending_cannot_be_downloaded(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        ticket = await AttachmentService(session, ready_store).begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="pending.txt",
            mime="text/plain",
            size=10,
        )
        with pytest.raises(NotFoundError):
            await AttachmentService(session, ready_store).download_url(
                uploader, ticket.attachment_id
            )


class TestSweep:
    async def test_removes_stale_pending_rows(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        """업로드 중 창을 닫으면 pending 행만 남는다. 안 치우면 계속 쌓인다."""
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="stale.txt",
            mime="text/plain",
            size=10,
        )
        assert await service.sweep_pending(older_than_seconds=3600) == 0

        removed = await service.sweep_pending(older_than_seconds=-1)
        assert removed >= 1
        await session.flush()
        assert await session.get(Attachment, ticket.attachment_id) is None

    async def test_leaves_ready_rows_alone(
        self, session: AsyncSession, ready_store: ObjectStore, uploader: Actor
    ) -> None:
        body = b"keep me"
        service = AttachmentService(session, ready_store)
        ticket = await service.begin(
            uploader,
            owner_type=OWNER_OPEN,
            owner_id=new_id(),
            filename="keep.txt",
            mime="text/plain",
            size=len(body),
        )
        await _put(ticket.upload_url, body, ticket.headers)
        await service.complete(uploader, ticket.attachment_id)

        await service.sweep_pending(older_than_seconds=-1)
        await session.flush()
        row = await session.get(Attachment, ticket.attachment_id)
        assert row is not None
        assert row.status == STATUS_READY


def test_status_constants_are_distinct() -> None:
    assert STATUS_PENDING != STATUS_READY
