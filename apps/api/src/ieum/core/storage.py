"""오브젝트 스토리지 (S3/MinIO).

**서버는 파일 바이트를 경유하지 않는다** (tech-stack.md). 업로드도 다운로드도
presigned URL 로 클라이언트가 직접 한다. 서버를 거치면 큰 파일 하나가 워커를
붙잡고, 메모리도 파일 크기만큼 든다.

서명은 로컬 계산이라 동기 boto3 로 충분하다. 실제 네트워크를 타는 것(HEAD·
DELETE)만 스레드로 넘긴다 — aiobotocore 는 botocore 를 좁게 핀해서 의존성
충돌을 자주 만든다.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ieum.core.logging import get_logger

if TYPE_CHECKING:
    from ieum.config import Settings

log = get_logger(__name__)

#: presigned URL 유효 시간. 짧게 둔다 — URL 이 로그·리퍼러로 새면 그대로
#: 파일 접근 권한이다.
UPLOAD_TTL_SECONDS = 300
DOWNLOAD_TTL_SECONDS = 300

#: 파일명에서 경로 조작과 제어 문자를 제거한다.
_UNSAFE = re.compile(r"[^\w.\- ]", re.UNICODE)


def safe_filename(name: str) -> str:
    """저장·다운로드에 쓸 이름. 비면 'file' 로 떨어진다.

    `../` 를 지우는 것만으로는 부족하다 — 개행이 섞이면
    Content-Disposition 헤더를 쪼갤 수 있다.
    """
    cleaned = _UNSAFE.sub("_", name.replace("/", "_").replace("\\", "_")).strip()
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    content_type: str


class ObjectStore:
    """S3 호환 스토리지. 버킷 하나만 쓴다."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = self._make_client(settings, settings.s3_endpoint_url)
        # presigned URL 은 **브라우저가** 연다. 컨테이너 안에서 보는 주소
        # (`http://minio:9000`)로 서명하면 브라우저는 그 호스트를 못 찾는다 —
        # compose 로 띄우면 첨부 업로드가 통째로 죽는다(실제로 그랬다).
        # 공개 주소가 따로 없으면 같은 클라이언트를 쓴다.
        public = settings.s3_public_endpoint_url
        self._signer = (
            self._client
            if not public or public == settings.s3_endpoint_url
            else self._make_client(settings, public)
        )

    @staticmethod
    def _make_client(settings: Settings, endpoint_url: str | None) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value() or None,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value() or None,
            # MinIO 는 path-style 만 안전하게 지원한다. 가상 호스트 방식은
            # 버킷 이름이 도메인에 들어가 로컬 개발에서 깨진다.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def upload_url(self, key: str, *, content_type: str, content_length: int) -> str:
        """업로드용 presigned PUT.

        content-length 까지 서명에 넣는다. PUT 은 POST 정책처럼 크기 범위를
        걸 수 없어서, 정확한 길이를 못 박는 게 유일한 사전 방어다. 클라이언트가
        다른 크기를 보내면 스토리지가 거절한다.
        """
        url: str = self._signer.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=UPLOAD_TTL_SECONDS,
        )
        return url

    def download_url(self, key: str, *, filename: str, inline: bool) -> str:
        """다운로드용 presigned GET.

        이미지가 아니면 `attachment` 로 강제한다. 업로드된 HTML 을 브라우저가
        인라인으로 그리면 우리 오리진에서 스크립트가 도는 것과 같다.
        """
        disposition = "inline" if inline else "attachment"
        safe = safe_filename(filename)
        url: str = self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f"{disposition}; filename*=UTF-8''{quote(safe, safe='')}"
                ),
            },
            ExpiresIn=DOWNLOAD_TTL_SECONDS,
        )
        return url

    async def head(self, key: str) -> ObjectInfo | None:
        """올라온 객체의 실제 크기·타입. 없으면 None."""

        def _head() -> ObjectInfo | None:
            try:
                response = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            return ObjectInfo(
                size=int(response["ContentLength"]),
                content_type=str(response.get("ContentType", "application/octet-stream")),
            )

        return await asyncio.to_thread(_head)

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """서버가 직접 쓴다.

        presigned PUT 은 **브라우저**가 올릴 때의 길이다. 서버가 이미 손에 쥔
        바이트를(ZIP 안의 그림 같은 것) 스스로에게 서명해 보내는 것은 왕복만
        늘린다.
        """

        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes | None:
        """객체를 통째로 읽는다. 없으면 None.

        내보내기가 쓴다. 첨부 상한이 있어 한 파일이 메모리를 삼키지 않는다.
        """

        def _get() -> bytes | None:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            body: bytes = response["Body"].read()
            return body

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def ensure_bucket(self) -> None:
        """개발·테스트 편의. 운영에서는 인프라가 미리 만든다."""

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)


__all__ = [
    "DOWNLOAD_TTL_SECONDS",
    "UPLOAD_TTL_SECONDS",
    "ObjectInfo",
    "ObjectStore",
    "safe_filename",
]
