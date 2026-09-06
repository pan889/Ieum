"""identity 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

GLOBAL = frozenset({ScopeKind.GLOBAL})

USER_VIEW = "identity.user.view"
USER_INVITE = "identity.user.invite"
USER_MANAGE = "identity.user.manage"
GROUP_MANAGE = "identity.group.manage"
SESSION_REVOKE = "identity.session.revoke"
TOKEN_ISSUE = "identity.token.issue"  # noqa: S105 - 권한 상수이지 비밀번호가 아니다
AUDIT_VIEW = "identity.audit.view"

registry.register_many(
    [
        PermissionDef(USER_VIEW, GLOBAL, "사용자 목록·상세 조회"),
        PermissionDef(USER_INVITE, GLOBAL, "사용자 초대"),
        # 계정 활성/정지와 MFA 해제는 계정 탈취 경로다. step-up 을 요구한다.
        PermissionDef(USER_MANAGE, GLOBAL, "사용자 수정·정지", requires_step_up=True),
        PermissionDef(GROUP_MANAGE, GLOBAL, "그룹 생성·멤버 변경"),
        PermissionDef(SESSION_REVOKE, GLOBAL, "타인 세션 폐기", requires_step_up=True),
        PermissionDef(TOKEN_ISSUE, GLOBAL, "API 토큰 발급", requires_step_up=True),
        PermissionDef(AUDIT_VIEW, GLOBAL, "감사 로그 조회"),
    ]
)

ALL = (
    USER_VIEW,
    USER_INVITE,
    USER_MANAGE,
    GROUP_MANAGE,
    SESSION_REVOKE,
    TOKEN_ISSUE,
    AUDIT_VIEW,
)
