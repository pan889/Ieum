"""FastAPI 의존성. 인증·세션·설정 배선만 한다 — 권한 검사는 서비스가 한다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.core.context import Actor
from ieum.core.exceptions import AuthenticationError
from ieum.core.permissions import PermissionService, get_permission_service
from ieum.core.storage import ObjectStore
from ieum.db.session import get_db_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
PermissionDep = Annotated[PermissionService, Depends(get_permission_service)]


def get_object_store(settings: AppSettings) -> ObjectStore:
    """요청마다 새로 만든다. boto3 클라이언트 생성은 순수 계산이라 싸고,
    설정이 바뀌면 다음 요청부터 반영된다."""
    return ObjectStore(settings)


StorageDep = Annotated[ObjectStore, Depends(get_object_store)]


def client_ip(request: Request) -> str | None:
    """프록시 뒤를 가정한다. X-Forwarded-For 의 첫 항목이 원 클라이언트다."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


ClientIp = Annotated[str | None, Depends(client_ip)]
UserAgent = Annotated[str | None, Header(alias="User-Agent")]


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authorization 헤더가 없다.")
    return authorization[7:].strip()


async def current_actor(
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """MFA 까지 완료된 액터. 일반 API 는 전부 이걸 쓴다."""
    from ieum.modules.identity.service import AuthService

    actor, _ = await AuthService(session, settings).authenticate_access_token(
        _bearer_token(authorization), require_mfa=True
    )
    return actor


async def current_actor_pending_mfa(
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """MFA 미완료라도 통과시킨다. 2FA 등록·검증 엔드포인트 전용이다."""
    from ieum.modules.identity.service import AuthService

    actor, _ = await AuthService(session, settings).authenticate_access_token(
        _bearer_token(authorization), require_mfa=False
    )
    return actor


CurrentActor = Annotated[Actor, Depends(current_actor)]
PendingMfaActor = Annotated[Actor, Depends(current_actor_pending_mfa)]
