"""org 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

GLOBAL = frozenset({ScopeKind.GLOBAL})
PROJECT = frozenset({ScopeKind.PROJECT})
GLOBAL_OR_PROJECT = frozenset({ScopeKind.GLOBAL, ScopeKind.PROJECT})

PROJECT_VIEW = "org.project.view"
PROJECT_CREATE = "org.project.create"
PROJECT_EDIT = "org.project.edit"
PROJECT_ARCHIVE = "org.project.archive"
PROJECT_ADMIN = "org.project.admin"
ROLE_MANAGE = "org.role.manage"
ROLE_ASSIGN = "org.role.assign"
SECURITY_MANAGE = "org.security.manage"

registry.register_many(
    [
        PermissionDef(PROJECT_VIEW, GLOBAL_OR_PROJECT, "프로젝트 조회"),
        PermissionDef(PROJECT_CREATE, GLOBAL, "프로젝트 생성"),
        PermissionDef(PROJECT_EDIT, PROJECT, "프로젝트 수정"),
        PermissionDef(PROJECT_ARCHIVE, PROJECT, "프로젝트 아카이브"),
        PermissionDef(PROJECT_ADMIN, PROJECT, "프로젝트 설정·권한 관리"),
        # 권한 스킴 변경은 되돌리기 어렵다. step-up 을 요구한다 (auth.md 3절).
        PermissionDef(ROLE_MANAGE, GLOBAL, "역할·권한 정의 변경", requires_step_up=True),
        PermissionDef(ROLE_ASSIGN, GLOBAL_OR_PROJECT, "역할 할당", requires_step_up=True),
        # step-up 을 **붙이지 않는다.** 2FA 강제를 켜려면 2FA 가 있어야 한다면,
        # 아직 아무도 2FA 를 안 쓰는 조직은 영영 켤 수 없다. 대신 끄는 쪽은
        # 서비스가 step-up 을 따로 요구한다 — 보호를 거는 일과 푸는 일은
        # 무게가 다르다.
        PermissionDef(SECURITY_MANAGE, GLOBAL, "보안 정책 변경"),
    ]
)

ALL = (
    PROJECT_VIEW,
    PROJECT_CREATE,
    PROJECT_EDIT,
    PROJECT_ARCHIVE,
    PROJECT_ADMIN,
    ROLE_MANAGE,
    ROLE_ASSIGN,
    SECURITY_MANAGE,
)
