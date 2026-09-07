"""FastAPI 의존성. 인증·세션·설정 배선만 한다 — 권한 검사는 서비스가 한다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.core.context import Actor
from ieum.core.exceptions import AuthenticationError, PermissionDeniedError
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


#: 고객(포털 사용자)에게 열린 네임스페이스 (auth.md 5절).
#:
#: `auth` 가 함께 있는 것은 **자기 세션과 자기 자격증명**을 다루는 자리이기
#: 때문이다 — 내부 제품 표면이 아니다. 빼 두면 두 가지가 깨진다:
#: 로그아웃(`/auth/logout` 은 액터를 요구한다)이 막혀 **로그인은 되고 나갈
#: 길이 없는** 계정이 되고, 조직 전체 2FA 강제가 켜진 설치에서는 고객이
#: 등록 엔드포인트(`/auth/mfa/*`)에 닿지 못해 **통째로 잠긴다** — 정책만
#: 켜고 등록할 길을 안 이어 두면 그건 락아웃 스위치라는, 이미 한 번 겪은
#: 실수와 같은 모양이다(auth.md 3절).
CUSTOMER_PREFIXES = ("/api/v1/portal/", "/api/v1/auth/")


def _refuse_customer_outside_portal(actor: Actor, request: Request) -> None:
    """고객 계정은 포털 밖을 볼 수 없다. **권한 검사 이전에** 막는다.

    권한 서비스도 고객을 거절하지만(`permissions.py`), 그건 권한을 **보는**
    라우트만 지킨다. 권한이 필요 없는 라우트는 그대로 열린다 — 실제로
    `/roles/permissions` 가 그랬다: 권한 정의 47개를 통째로 내주고 있었고,
    그 라우트는 PAT 화면이 스코프 후보를 그리려고 일부러 권한 없이 열어
    둔 것이다.

    라우트마다 검사를 더하는 방식은 다음에 추가되는 라우트에서 또 뚫린다.
    그래서 **기본 거절**로 뒤집는다: 액터를 만드는 자리에서 경로를 보고,
    허용된 접두사가 아니면 고객을 통과시키지 않는다.

    404 가 아니라 403 을 준다. 경로의 존재 여부는 이미 공개된 스키마에
    있으므로 감출 것이 없고, "권한이 없다" 가 사실이다.
    """
    if not actor.is_customer:
        return
    if request.url.path.startswith(CUSTOMER_PREFIXES):
        return
    raise PermissionDeniedError("포털 사용자는 이 API 를 쓸 수 없다.")


async def current_actor(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """MFA 까지 완료된 액터. 일반 API 는 전부 이걸 쓴다.

    세션 액세스 토큰과 PAT 을 모두 받는다. 어느 쪽인지는 **접두사로** 가른다 —
    양쪽 테이블을 다 뒤지면 요청마다 쿼리가 하나씩 늘고, 실패 응답 시간으로
    "이 토큰이 어느 종류인지" 가 새어 나간다.
    """
    from ieum.modules.identity.service import ApiTokenService, AuthService

    raw = _bearer_token(authorization)
    if ApiTokenService.looks_like_token(raw):
        token_actor = await ApiTokenService(session, settings).authenticate(raw)
        _refuse_customer_outside_portal(token_actor, request)
        return token_actor

    actor, _ = await AuthService(session, settings).authenticate_access_token(raw, require_mfa=True)
    _refuse_customer_outside_portal(actor, request)
    return actor


async def current_actor_pending_mfa(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """MFA 미완료라도 통과시킨다. 2FA 등록·검증 엔드포인트 전용이다."""
    from ieum.modules.identity.service import AuthService

    actor, _ = await AuthService(session, settings).authenticate_access_token(
        _bearer_token(authorization), require_mfa=False
    )
    # 여기도 막는다. 2FA 경로는 권한을 보지 않으므로, 빼 두면 고객이 내부
    # 2FA 엔드포인트를 그대로 쓴다.
    _refuse_customer_outside_portal(actor, request)
    return actor


CurrentActor = Annotated[Actor, Depends(current_actor)]
PendingMfaActor = Annotated[Actor, Depends(current_actor_pending_mfa)]
