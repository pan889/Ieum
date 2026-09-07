"""identity HTTP 라우터. 얇게 유지한다 — 스키마 검증과 서비스 호출뿐."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse

from ieum.config import get_settings
from ieum.core.context import Actor
from ieum.core.deps import (
    AppSettings,
    ClientIp,
    CurrentActor,
    DbSession,
    PendingMfaActor,
    PermissionDep,
    UserAgent,
)
from ieum.core.exceptions import AuthenticationError, ValidationError
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.core.permissions import Scope, get_permission_service
from ieum.core.time import utcnow
from ieum.modules.identity import audit as audit_log
from ieum.modules.identity import permissions as perms
from ieum.modules.identity import saml
from ieum.modules.identity.repository import AuditFilter
from ieum.modules.identity.schemas import (
    AcceptInviteRequest,
    ApiTokenCreateRequest,
    ApiTokenIssuedResponse,
    ApiTokenResponse,
    AuditLogResponse,
    AuditPageResponse,
    BackupCodesResponse,
    ChangePasswordRequest,
    GroupCreateRequest,
    GroupMemberRequest,
    GroupResponse,
    GroupUpdateRequest,
    IdpResponse,
    InviteRequest,
    LoginRequest,
    MFACredentialResponse,
    MFAVerifyRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    SamlHandoffRequest,
    SamlProviderCreateRequest,
    SamlProviderUpdateRequest,
    SessionResponse,
    SsoCallbackRequest,
    SsoProviderCreateRequest,
    SsoProviderResponse,
    SsoStartResponse,
    TokenResponse,
    TOTPEnrollResponse,
    UserAdminUpdateRequest,
    UserPageResponse,
    UserResponse,
    WebAuthnOptionsResponse,
    WebAuthnResponseRequest,
)
from ieum.modules.identity.service import (
    ApiTokenService,
    AuditService,
    AuthService,
    GroupService,
    IdentityProviderService,
    IssuedTokens,
    MFAService,
    NewProvider,
    NewSamlProvider,
    SamlService,
    SsoService,
    UserService,
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
groups_router = APIRouter(prefix="/groups", tags=["groups"])
audit_router = APIRouter(prefix="/audit", tags=["audit"])
#: SAML 콜백이 도착할 화면 경로. **서버가 정한다** — ACS 가 브라우저를 여기로
#: 되돌린다. 화면 쪽 라우트와 짝이다.
SAML_CALLBACK_PATH = "/auth/saml/callback"

sso_admin_router = APIRouter(prefix="/admin/sso", tags=["sso"])


def _tokens(issued: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        mfa_required=issued.mfa_required,
        mfa_enrollment_required=issued.mfa_enrollment_required,
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
    try:
        issued = await AuthService(session, settings).login(
            email=body.email, password=body.password, ip=ip, user_agent=user_agent
        )
    except Exception:
        # 실패 기록은 커밋돼야 한다. 실패 경로는 예외를 던지고 그러면 요청
        # 세션이 롤백되는데, 그때 시도 기록까지 함께 사라지면 실패 횟수를 셀
        # 수 없다 — 무차별 대입 제한이 통째로 동작하지 않는다. 감사 로그에도
        # 실패한 로그인이 안 남는다(auth.md 6절). 리프레시·MFA 와 같은 처리다.
        await session.commit()
        raise
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


@auth_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: UUID, actor: CurrentActor, session: DbSession, settings: AppSettings
) -> None:
    """기기 하나만 끊는다. 전부 끊으면 지금 쓰는 자리에서도 튕겨 나간다.

    남의 세션인지는 서비스가 본다. 없거나 남의 것이면 조용히 204 다 —
    404 를 주면 세션 id 를 넣어 보며 남의 세션 존재를 확인할 수 있다.
    """
    await AuthService(session, settings).revoke_session(
        session_id=session_id, user_id=actor.user_id, actor_id=actor.user_id
    )
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
        # 강제 등록 흐름에서 이 세션을 바로 열어 준다. 방금 맞힌 코드가
        # 소지 증명인데 또 물으면 사람은 "안 되는구나" 로 읽는다.
        session_id=actor.session_id,
    )
    await session.commit()


def _session_of(actor: Actor) -> UUID:
    """인증기를 다루려면 **세션이 있어야 한다.**

    챌린지는 세션에 적힌다 — 그것이 "이 브라우저가 지금 키를 만졌다" 를
    보장하는 방식이다. API 토큰에는 세션이 없고, 있어도 인증기를 꽂을 브라우저가
    없다. step-up 이 토큰을 거절하는 것과 같은 이유다.
    """
    if actor.session_id is None:
        raise AuthenticationError(
            "이 작업은 API 토큰으로 할 수 없다.", code="auth.step_up_not_available_for_token"
        )
    return actor.session_id


@auth_router.post("/mfa/webauthn/register/start", response_model=WebAuthnOptionsResponse)
async def start_webauthn_registration(
    actor: PendingMfaActor, session: DbSession, settings: AppSettings
) -> WebAuthnOptionsResponse:
    """등록 옵션. 챌린지는 **이 세션에** 적힌다."""
    options = await MFAService(session, settings).start_webauthn_registration(
        user_id=actor.user_id,
        session_id=_session_of(actor),
        mfa_satisfied=actor.mfa_satisfied_at is not None,
    )
    await session.commit()
    return WebAuthnOptionsResponse(options=options)


@auth_router.post(
    "/mfa/webauthn/register/finish",
    response_model=MFACredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def finish_webauthn_registration(
    body: WebAuthnResponseRequest,
    actor: PendingMfaActor,
    session: DbSession,
    settings: AppSettings,
) -> MFACredentialResponse:
    """등록을 마친다. 방금 인증기를 만진 것이 소지 증명이므로 이 세션도 열린다."""
    credential = await MFAService(session, settings).confirm_webauthn_registration(
        user_id=actor.user_id,
        session_id=_session_of(actor),
        response_json=body.response,
        mfa_satisfied=actor.mfa_satisfied_at is not None,
        label=body.label,
    )
    await session.commit()
    return MFACredentialResponse.model_validate(credential)


@auth_router.post("/mfa/webauthn/verify/start", response_model=WebAuthnOptionsResponse)
async def start_webauthn_authentication(
    actor: PendingMfaActor, session: DbSession, settings: AppSettings
) -> WebAuthnOptionsResponse:
    options = await MFAService(session, settings).start_webauthn_authentication(
        user_id=actor.user_id, session_id=_session_of(actor)
    )
    await session.commit()
    return WebAuthnOptionsResponse(options=options)


@auth_router.post("/mfa/webauthn/verify/finish", status_code=status.HTTP_204_NO_CONTENT)
async def finish_webauthn_authentication(
    body: WebAuthnResponseRequest,
    actor: PendingMfaActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    await MFAService(session, settings).verify_webauthn(
        user_id=actor.user_id, session_id=_session_of(actor), response_json=body.response
    )
    await session.commit()


@auth_router.get("/mfa/credentials", response_model=list[MFACredentialResponse])
async def list_mfa_credentials(
    actor: PendingMfaActor, session: DbSession, settings: AppSettings
) -> list[MFACredentialResponse]:
    """내가 등록한 2차 요소. 미완료 세션도 볼 수 있어야 한다 — 무엇으로
    인증할 수 있는지 모르면 확인 화면이 무엇을 보여줄지 정할 수 없다."""
    rows = await MFAService(session, settings).list_credentials(user_id=actor.user_id)
    return [MFACredentialResponse.model_validate(c) for c in rows]


@auth_router.delete("/mfa/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_mfa_credential(
    credential_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """인증기를 뗀다. **MFA 를 통과한 세션만** — 비밀번호만 아는 공격자가
    남의 인증기를 떼면 그것이 곧 2차 요소 해제다."""
    await MFAService(session, settings).remove_credential(
        user_id=actor.user_id, credential_id=credential_id
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


@users_router.patch("/me", response_model=UserResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
) -> UserResponse:
    """언어 설정. 브라우저가 아니라 서버가 기억한다 — 다른 기기에서도 같은
    언어로 열리고, 알림 메일도 이 값으로 렌더된다."""
    user = await UserService(session, settings).update_profile(
        user_id=actor.user_id, locale=body.locale
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


# ── 감사 로그 ──────────────────────────────────────────────────


def _audit_filter(
    actor_id: UUID | None,
    action: str | None,
    target_type: str | None,
    target_id: UUID | None,
    since: datetime | None,
    until: datetime | None,
) -> AuditFilter:
    return AuditFilter(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        since=since,
        until=until,
    )


@audit_router.get("/actions", response_model=list[str])
async def list_audit_actions(actor: CurrentActor, session: DbSession) -> list[str]:
    """필터에 쓸 행동 목록. 상수에서 나온다.

    테이블에서 `DISTINCT action` 을 긁지 않는다 — 행이 쌓이면 그 한 번이
    인덱스를 통째로 훑고, 아직 한 번도 안 일어난 행동은 목록에서 빠져
    "그런 건 기록 안 하나" 로 읽힌다.
    """
    # 목록 자체가 우리가 무엇을 감시하는지 알려 준다. 권한을 본다.
    await get_permission_service().require(session, actor, perms.AUDIT_VIEW, scope=Scope.global_())
    return list(audit_log.ACTIONS)


@audit_router.get("", response_model=AuditPageResponse)
async def list_audit(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    actor_id: UUID | None = None,
    action: str | None = Query(default=None, max_length=100),
    target_type: str | None = Query(default=None, max_length=64),
    target_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> AuditPageResponse:
    """최신순. 감사 로그는 늘 "방금 무슨 일이 있었나" 부터 본다."""
    page, emails = await AuditService(session, permissions).list(
        actor,
        _audit_filter(actor_id, action, target_type, target_id, since, until),
        PageRequest(limit=limit, cursor=cursor),
    )
    return AuditPageResponse(
        items=[
            AuditLogResponse.of(row, emails.get(row.actor_id) if row.actor_id else None)
            for row in page.items
        ],
        next_cursor=page.next_cursor,
    )


@audit_router.get("/export")
async def export_audit(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    actor_id: UUID | None = None,
    action: str | None = Query(default=None, max_length=100),
    target_type: str | None = Query(default=None, max_length=64),
    target_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    """같은 필터로 CSV. 감사 대응은 화면이 아니라 파일로 끝난다."""
    stream = await AuditService(session, permissions).export(
        actor, _audit_filter(actor_id, action, target_type, target_id, since, until)
    )
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ieum-audit-{stamp}.csv"',
            # 감사 로그는 사용자별 ACL 을 탄다. 중간 캐시에 남으면 안 된다.
            "Cache-Control": "no-store",
        },
    )


@users_router.delete("/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> None:
    """남의 세션을 원격으로 끊는다. 퇴사·기기 분실 때 관리자가 하는 일이다.

    step-up 을 요구한다(권한 정의에 붙어 있다) — 남의 자리에서 사람을
    쫓아내는 일이라, 자리를 비운 관리자 화면으로는 못 하게 한다.
    """
    await permissions.require(session, actor, perms.SESSION_REVOKE, scope=Scope.global_())
    await AuthService(session, settings).revoke_all_sessions(
        user_id=user_id, actor_id=actor.user_id
    )
    await session.commit()


@users_router.post("/{user_id}/suspend", response_model=UserResponse)
async def suspend_user(
    user_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> UserResponse:
    """계정을 정지한다. 세션도 함께 끊는다.

    step-up 이 붙어 있다(`USER_MANAGE`) — 남의 계정을 잠그는 일이라, 자리를
    비운 관리자 화면으로는 못 하게 한다.
    """
    await permissions.require(session, actor, perms.USER_MANAGE, scope=Scope.global_())
    user = await UserService(session, settings).set_status(
        actor_id=actor.user_id, user_id=user_id, suspend=True
    )
    await session.commit()
    return UserResponse.model_validate(user)


@users_router.post("/{user_id}/reactivate", response_model=UserResponse)
async def reactivate_user(
    user_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> UserResponse:
    await permissions.require(session, actor, perms.USER_MANAGE, scope=Scope.global_())
    user = await UserService(session, settings).set_status(
        actor_id=actor.user_id, user_id=user_id, suspend=False
    )
    await session.commit()
    return UserResponse.model_validate(user)


@users_router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UserAdminUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> UserResponse:
    """관리자가 남의 계정 설정을 바꾼다. 지금은 2FA 강제 하나뿐이다.

    `/users/me` 보다 **뒤에** 선언돼 있어야 한다. 앞에 오면 `me` 가 UUID
    로 파싱되지 않아 422 가 된다.
    """
    await permissions.require(session, actor, perms.USER_MANAGE, scope=Scope.global_())
    user = await UserService(session, settings).set_require_mfa(
        actor_id=actor.user_id, user_id=user_id, required=body.require_mfa
    )
    await session.commit()
    return UserResponse.model_validate(user)


# ── 그룹 ───────────────────────────────────────────────────────


@groups_router.get("", response_model=list[GroupResponse])
async def list_groups(
    actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[GroupResponse]:
    rows = await GroupService(session, permissions).list_all(actor)
    return [GroupResponse.of(group, count) for group, count in rows]


@groups_router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> GroupResponse:
    group = await GroupService(session, permissions).create(
        actor, name=body.name, description=body.description
    )
    await session.commit()
    return GroupResponse.of(group, 0)


@groups_router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: UUID,
    body: GroupUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> GroupResponse:
    service = GroupService(session, permissions)
    group = await service.update(actor, group_id, name=body.name, description=body.description)
    members = await service.members(actor, group_id)
    await session.commit()
    return GroupResponse.of(group, len(members))


@groups_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await GroupService(session, permissions).delete(actor, group_id)
    await session.commit()


@groups_router.get("/{group_id}/members", response_model=list[UserResponse])
async def list_group_members(
    group_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[UserResponse]:
    members = await GroupService(session, permissions).members(actor, group_id)
    return [UserResponse.model_validate(user) for user in members]


@groups_router.post("/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def add_group_member(
    group_id: UUID,
    body: GroupMemberRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await GroupService(session, permissions).add_member(actor, group_id, body.user_id)
    await session.commit()


@groups_router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_group_member(
    group_id: UUID,
    user_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await GroupService(session, permissions).remove_member(actor, group_id, user_id)
    await session.commit()


# ── SSO (OIDC) ─────────────────────────────────────────────────


@auth_router.get("/sso/providers", response_model=list[SsoProviderResponse])
async def list_sso_providers(session: DbSession) -> list[SsoProviderResponse]:
    """로그인 **전에** 부른다. 인증을 걸면 SSO 버튼을 못 그린다.

    이름과 id 만 준다. 익명에게 열린 목록이라 엔드포인트·클레임 설정까지
    흘리면 조직의 IdP 구성이 통째로 드러난다.
    """
    rows = await SsoService(session, get_settings()).providers()
    return [SsoProviderResponse(id=p.id, name=p.name, kind=p.kind) for p in rows]


@auth_router.post("/sso/{provider_id}/start", response_model=SsoStartResponse)
async def start_sso(
    provider_id: UUID, session: DbSession, settings: AppSettings
) -> SsoStartResponse:
    """인가 URL 을 만든다. 전이 상태는 `state` 에 봉해 나간다.

    콜백 주소는 **서버가 정한다.** 클라이언트가 고르게 하면 그 값을 자기
    주소로 바꿔 인가 코드를 가져갈 수 있다.
    """
    url = await SsoService(session, settings).begin(provider_id)
    return SsoStartResponse(authorization_url=url)


@auth_router.post("/sso/callback", response_model=TokenResponse)
async def complete_sso(
    body: SsoCallbackRequest,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent = None,
) -> TokenResponse:
    """IdP 가 돌려준 코드로 우리 세션을 연다."""
    issued = await SsoService(session, settings).complete(
        code=body.code, state=body.state, ip=ip, user_agent=user_agent
    )
    await session.commit()
    return _tokens(issued)


# ── SAML ───────────────────────────────────────────────────────


@auth_router.post("/saml/{provider_id}/start", response_model=SsoStartResponse)
async def start_saml(
    provider_id: UUID, session: DbSession, settings: AppSettings
) -> SsoStartResponse:
    """AuthnRequest 를 보낼 주소. 요청 ID 를 기록해 두고 돌아올 때 맞춘다."""
    url = await SamlService(session, settings).begin(provider_id)
    await session.commit()
    return SsoStartResponse(authorization_url=url)


@auth_router.post("/saml/acs", include_in_schema=False)
async def saml_acs(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
) -> RedirectResponse:
    """IdP 가 브라우저를 통해 어설션을 POST 하는 자리.

    **화면이 부르는 API 가 아니다.** IdP 가 `application/x-www-form-urlencoded`
    로 보내므로 폼을 직접 읽고, 응답은 브라우저를 화면으로 되돌리는
    리다이렉트다. 여기서 JSON 을 돌려주면 사용자는 날 JSON 을 보게 된다.

    토큰은 주소에 싣지 않는다. 1회용 코드만 넘기고 화면이 바꿔 간다.
    """
    form = await request.form()
    saml_response = str(form.get("SAMLResponse") or "")
    relay_state = str(form.get("RelayState") or "") or None
    if not saml_response:
        raise ValidationError("SAMLResponse 가 없다.", code="auth.saml_missing_response")

    code = await SamlService(session, settings).accept_response(
        saml_response=saml_response, relay_state=relay_state, ip=ip
    )
    await session.commit()
    landing = f"{settings.base_url.rstrip('/')}{SAML_CALLBACK_PATH}?code={quote(code, safe='')}"
    # 303: POST 를 GET 으로 바꿔 되돌린다. 302 로 두면 브라우저가 POST 를
    # 그대로 다시 보내는 경우가 있다.
    return RedirectResponse(landing, status_code=status.HTTP_303_SEE_OTHER)


@auth_router.post("/saml/exchange", response_model=TokenResponse)
async def exchange_saml(
    body: SamlHandoffRequest,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent = None,
) -> TokenResponse:
    """1회용 코드를 세션으로. 두 번째는 거절한다."""
    issued = await SamlService(session, settings).exchange(body.code, ip=ip, user_agent=user_agent)
    await session.commit()
    return _tokens(issued)


@auth_router.get("/saml/{provider_id}/metadata", include_in_schema=False)
async def saml_metadata(provider_id: UUID, session: DbSession, settings: AppSettings) -> Response:
    """IdP 에 등록할 SP 메타데이터.

    익명으로 열어 둔다 — IdP 쪽 관리자가 우리 로그인 계정 없이 가져가야 하고,
    안에 든 것은 우리 EntityID·ACS 주소·공개 인증서뿐이다.
    """
    xml = await SamlService(session, settings).metadata(provider_id)
    return Response(content=xml, media_type="application/samlmetadata+xml")


# ── IdP 관리 ───────────────────────────────────────────────────


@sso_admin_router.get("/providers", response_model=list[IdpResponse])
async def list_providers(
    actor: CurrentActor, session: DbSession, settings: AppSettings, permissions: PermissionDep
) -> list[IdpResponse]:
    rows = await IdentityProviderService(session, settings, permissions).list_all(actor)
    return [IdpResponse.of(p) for p in rows]


@sso_admin_router.post(
    "/providers", response_model=IdpResponse, status_code=status.HTTP_201_CREATED
)
async def create_provider(
    body: SsoProviderCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> IdpResponse:
    """IdP 를 등록한다. step-up 이 필요하다 — 이 설정을 쥐면 누구로든 로그인할
    수 있다(발급자와 JWKS 를 바꾸면 자기 키로 서명한 토큰이 통과한다)."""
    provider = await IdentityProviderService(session, settings, permissions).create(
        actor,
        NewProvider(
            name=body.name,
            issuer=body.issuer,
            client_id=body.client_id,
            client_secret=body.client_secret,
            authorization_endpoint=body.authorization_endpoint,
            token_endpoint=body.token_endpoint,
            jwks_uri=body.jwks_uri,
            scopes=body.scopes,
            email_claim=body.email_claim,
            name_claim=body.name_claim,
            groups_claim=body.groups_claim,
            jit_provisioning=body.jit_provisioning,
            link_verified_email=body.link_verified_email,
            email_domains=tuple(body.email_domains),
            trust_idp_mfa=body.trust_idp_mfa,
        ),
    )
    await session.commit()
    return IdpResponse.of(provider)


@sso_admin_router.post(
    "/saml/providers", response_model=IdpResponse, status_code=status.HTTP_201_CREATED
)
async def create_saml_provider(
    body: SamlProviderCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> IdpResponse:
    """SAML IdP 를 등록한다. step-up 이 필요하다 — OIDC 와 같은 이유다.

    메타데이터 XML 을 붙이면 발급자·SSO 주소·인증서를 거기서 읽는다. 셋을
    손으로 옮겨 적는 동안 한 글자가 틀리면, 로그인이 안 되는 이유가
    "인증서가 틀렸다" 로만 보인다.
    """
    if body.metadata_xml:
        read = saml.read_idp_metadata(body.metadata_xml)
        entity_id, sso_url = read.entity_id, read.sso_url
        certificates = read.certificates
    else:
        entity_id = (body.entity_id or "").strip()
        sso_url = (body.sso_url or "").strip()
        certificates = tuple(saml.normalize_certificate(c) for c in body.certificates)
        if not (entity_id and sso_url):
            raise ValidationError(
                "메타데이터 XML 이 없으면 발급자와 SSO 주소를 직접 줘야 한다.",
                code="identity.saml_metadata_required",
            )

    provider = await IdentityProviderService(session, settings, permissions).create_saml(
        actor,
        NewSamlProvider(
            name=body.name,
            entity_id=entity_id,
            sso_url=sso_url,
            certificates=certificates,
            sp_private_key=body.sp_private_key,
            sp_certificate=body.sp_certificate,
            want_encrypted=body.want_encrypted,
            allow_idp_initiated=body.allow_idp_initiated,
            email_attribute=body.email_attribute,
            name_attribute=body.name_attribute,
            groups_attribute=body.groups_attribute,
            jit_provisioning=body.jit_provisioning,
            link_verified_email=body.link_verified_email,
            email_domains=tuple(body.email_domains),
        ),
    )
    await session.commit()
    return IdpResponse.of(provider)


@sso_admin_router.patch("/saml/providers/{provider_id}", response_model=IdpResponse)
async def update_saml_provider(
    provider_id: UUID,
    body: SamlProviderUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> IdpResponse:
    """서명 인증서·SSO 주소를 갈아 끼운다. IdP 는 키를 돌린다 — 갈아 끼울 길이
    없으면 교체하는 날 로그인이 끊기고 되돌릴 방법도 없다."""
    entity_id: str | None = None
    sso_url = (body.sso_url or "").strip() or None
    certificates = tuple(saml.normalize_certificate(c) for c in body.certificates)
    if body.metadata_xml:
        read = saml.read_idp_metadata(body.metadata_xml)
        entity_id, sso_url = read.entity_id, read.sso_url
        certificates = read.certificates

    provider = await IdentityProviderService(session, settings, permissions).update_saml(
        actor, provider_id, entity_id=entity_id, sso_url=sso_url, certificates=certificates
    )
    await session.commit()
    return IdpResponse.of(provider)


@sso_admin_router.post("/providers/{provider_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_provider(
    provider_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> None:
    """지우지 않고 끈다. 지우면 `user_identity` 가 따라 사라져, 다시 켤 때
    모두가 새 계정으로 들어온다."""
    await IdentityProviderService(session, settings, permissions).set_enabled(
        actor, provider_id, enabled=False
    )
    await session.commit()


@sso_admin_router.post("/providers/{provider_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_provider(
    provider_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    settings: AppSettings,
    permissions: PermissionDep,
) -> None:
    """다시 켠다. **이 문이 없으면 끄는 것이 일방통행이다** — 같은 발급자로
    새로 등록하는 길은 유일 제약이 막는다."""
    await IdentityProviderService(session, settings, permissions).set_enabled(
        actor, provider_id, enabled=True
    )
    await session.commit()
