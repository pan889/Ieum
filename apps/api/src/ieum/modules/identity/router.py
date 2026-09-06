"""identity HTTP 라우터. 얇게 유지한다 — 스키마 검증과 서비스 호출뿐."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ieum.core.deps import (
    AppSettings,
    ClientIp,
    CurrentActor,
    DbSession,
    PendingMfaActor,
    PermissionDep,
    UserAgent,
)
from ieum.core.exceptions import ValidationError
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.core.permissions import Scope
from ieum.modules.identity import permissions as perms
from ieum.modules.identity.schemas import (
    AcceptInviteRequest,
    ApiTokenCreateRequest,
    ApiTokenIssuedResponse,
    ApiTokenResponse,
    BackupCodesResponse,
    ChangePasswordRequest,
    InviteRequest,
    LoginRequest,
    MFAVerifyRequest,
    RefreshRequest,
    SessionResponse,
    TokenResponse,
    TOTPEnrollResponse,
    UserPageResponse,
    UserResponse,
)
from ieum.modules.identity.service import (
    ApiTokenService,
    AuthService,
    IssuedTokens,
    MFAService,
    UserService,
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _tokens(issued: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        mfa_required=issued.mfa_required,
    )


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent = None,
) -> TokenResponse:
    """로컬 로그인. 계정 존재 여부가 응답으로 새지 않는다."""
    issued = await AuthService(session, settings).login(
        email=body.email, password=body.password, ip=ip, user_agent=user_agent
    )
    await session.commit()
    return _tokens(issued)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent = None,
) -> TokenResponse:
    """리프레시 로테이션. 재사용이 감지되면 세션 계열 전체가 폐기된다."""
    service = AuthService(session, settings)
    try:
        issued = await service.refresh(
            refresh_token=body.refresh_token, ip=ip, user_agent=user_agent
        )
    except Exception:
        # 재사용 탐지의 폐기 기록은 커밋돼야 한다. 롤백하면 탐지가 무의미해진다.
        await session.commit()
        raise
    await session.commit()
    return _tokens(issued)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(actor: PendingMfaActor, session: DbSession, settings: AppSettings) -> None:
    if actor.session_id is not None:
        await AuthService(session, settings).logout(session_id=actor.session_id)
    await session.commit()


@auth_router.get("/me", response_model=UserResponse)
async def me(actor: CurrentActor, session: DbSession) -> UserResponse:
    from ieum.modules.identity.repository import UserRepository

    user = await UserRepository(session).get(actor.user_id)
    assert user is not None  # 액터가 있으면 사용자도 있다
    return UserResponse.model_validate(user)


@auth_router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(actor: CurrentActor, session: DbSession) -> list[SessionResponse]:
    from ieum.modules.identity.repository import SessionRepository

    rows = await SessionRepository(session).list_live_for_user(actor.user_id)
    return [
        SessionResponse.model_validate(r).model_copy(
            update={"is_current": r.id == actor.session_id}
        )
        for r in rows
    ]


@auth_router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(
    actor: CurrentActor, session: DbSession, settings: AppSettings
) -> None:
    """본인의 모든 세션을 끊는다. 기기 분실 시의 첫 대응이다."""
    await AuthService(session, settings).revoke_all_sessions(user_id=actor.user_id)
    await session.commit()


# ── 2FA ────────────────────────────────────────────────────────
# MFA 미완료 세션도 접근할 수 있어야 한다. 그래야 강제 등록 흐름이 성립한다.


@auth_router.post("/mfa/totp/enroll", response_model=TOTPEnrollResponse)
async def enroll_totp(
    actor: PendingMfaActor, session: DbSession, settings: AppSettings
) -> TOTPEnrollResponse:
    enrollment = await MFAService(session, settings).start_totp_enrollment(
        user_id=actor.user_id, mfa_satisfied=actor.mfa_satisfied_at is not None
    )
    await session.commit()
    return TOTPEnrollResponse(
        credential_id=enrollment.credential_id,
        secret=enrollment.secret,
        provisioning_uri=enrollment.provisioning_uri,
        qr_svg=enrollment.qr_svg,
    )


@auth_router.post("/mfa/totp/{credential_id}/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_totp(
    credential_id: UUID,
    body: MFAVerifyRequest,
    actor: PendingMfaActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    await MFAService(session, settings).confirm_totp_enrollment(
        user_id=actor.user_id,
        credential_id=credential_id,
        code=body.code,
        mfa_satisfied=actor.mfa_satisfied_at is not None,
    )
    await session.commit()


@auth_router.post("/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_mfa(
    body: MFAVerifyRequest,
    actor: PendingMfaActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """로그인 후 2FA 확인. 성공하면 이 세션의 모든 API 가 열린다."""
    assert actor.session_id is not None
    service = MFAService(session, settings)
    try:
        await service.verify(user_id=actor.user_id, session_id=actor.session_id, code=body.code)
    except Exception:
        await session.commit()  # 실패 감사 기록을 남긴다
        raise
    await session.commit()


@auth_router.post("/mfa/backup-codes", response_model=BackupCodesResponse)
async def issue_backup_codes(
    actor: CurrentActor, session: DbSession, settings: AppSettings
) -> BackupCodesResponse:
    """10개를 발급한다. 이 응답 이후로는 서버도 평문을 모른다.

    MFA 를 통과한 세션 전용이다 — PendingMfaActor 를 쓰면 2FA 우회가 된다.
    """
    codes = await MFAService(session, settings).issue_backup_codes(
        user_id=actor.user_id, mfa_satisfied=actor.mfa_satisfied_at is not None
    )
    await session.commit()
    return BackupCodesResponse(codes=codes)


# ── 사용자 ──────────────────────────────────────────────────────


@users_router.get("", response_model=UserPageResponse)
async def list_users(
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
    q: str | None = None,
    ids: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> UserPageResponse:
    """담당자·멘션 피커가 쓰는 사용자 목록.

    `ids` 는 쉼표로 구분한 UUID 목록이다. 목록 화면이 이미 아는 담당자 id 를
    이름으로 바꿀 때 쓴다 — 전체를 훑지 않아도 된다.
    """
    await permissions.require(session, actor, perms.USER_VIEW, scope=Scope.global_())
    parsed: list[UUID] | None = None
    if ids is not None:
        try:
            parsed = [UUID(raw) for raw in ids.split(",") if raw.strip()]
        except ValueError as exc:
            raise ValidationError(
                "ids 는 쉼표로 구분한 UUID 목록이어야 한다.", code="common.validation_failed"
            ) from exc
        if not parsed:
            return UserPageResponse(items=[])

    page = await UserService(session, settings).directory(
        PageRequest(limit=limit, cursor=cursor), query=q, ids=parsed
    )
    return UserPageResponse(
        items=[UserResponse.model_validate(u) for u in page.items],
        next_cursor=page.next_cursor,
    )


@users_router.post("/invite", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> UserResponse:
    await permissions.require(session, actor, perms.USER_INVITE, scope=Scope.global_())
    user = await UserService(session, settings).invite(
        email=body.email,
        display_name=body.display_name,
        locale=body.locale,
        timezone=body.timezone,
        invited_by=actor.user_id,
    )
    await session.commit()
    return UserResponse.model_validate(user)


@users_router.post("/accept-invite", response_model=UserResponse)
async def accept_invite(
    body: AcceptInviteRequest, session: DbSession, settings: AppSettings
) -> UserResponse:
    from ieum.modules.identity.invites import decode_invite_token

    user_id = decode_invite_token(body.token, settings)
    user = await UserService(session, settings).activate_with_password(
        user_id=user_id, password=body.password
    )
    await session.commit()
    return UserResponse.model_validate(user)


@users_router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """비밀번호를 바꾸면 다른 기기의 세션이 전부 끊긴다."""
    await UserService(session, settings).change_password(
        user_id=actor.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    await session.commit()


# ── 개인 액세스 토큰 (PAT) ──────────────────────────────────────

tokens_router = APIRouter(prefix="/tokens", tags=["tokens"])


@tokens_router.post("", response_model=ApiTokenIssuedResponse, status_code=status.HTTP_201_CREATED)
async def issue_token(
    body: ApiTokenCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> ApiTokenIssuedResponse:
    """토큰 발급. step-up 이 필요하다 (auth.md 3절).

    PAT 은 step-up 을 통과할 수 없으므로, 토큰으로 토큰을 발급할 수도 없다 —
    하나가 새면 무한히 늘어나는 것을 막는다.
    """
    await permissions.require(session, actor, perms.TOKEN_ISSUE, scope=Scope.global_())
    row, secret = await ApiTokenService(session, settings).issue(
        actor,
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
    )
    await session.commit()
    return ApiTokenIssuedResponse(token=ApiTokenResponse.model_validate(row), secret=secret)


@tokens_router.get("", response_model=list[ApiTokenResponse])
async def list_tokens(
    actor: CurrentActor, session: DbSession, settings: AppSettings
) -> list[ApiTokenResponse]:
    """내 토큰. 평문은 없다 — 해시만 저장한다."""
    rows = await ApiTokenService(session, settings).list_for(actor)
    return [ApiTokenResponse.model_validate(r) for r in rows]


@tokens_router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: UUID, actor: CurrentActor, session: DbSession, settings: AppSettings
) -> None:
    """폐기는 step-up 없이 할 수 있다. 잠그는 쪽은 언제나 쉬워야 한다."""
    await ApiTokenService(session, settings).revoke(actor, token_id)
    await session.commit()
