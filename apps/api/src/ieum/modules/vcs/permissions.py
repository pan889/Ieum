"""vcs 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

GLOBAL_OR_PROJECT = frozenset({ScopeKind.GLOBAL, ScopeKind.PROJECT})

REPO_VIEW = "vcs.repository.view"
REPO_MANAGE = "vcs.repository.manage"

registry.register_many(
    [
        PermissionDef(REPO_VIEW, GLOBAL_OR_PROJECT, "연동한 저장소 목록 조회"),
        # 저장소를 등록하는 것은 **바깥에서 우리 이슈에 글을 붙이는 길**을
        # 여는 일이다. 시크릿을 아는 쪽이 커밋 제목과 주소를 이슈에 남길 수
        # 있으므로, 몰래 등록되지 않게 step-up 을 요구한다 (웹훅과 같다).
        PermissionDef(REPO_MANAGE, GLOBAL_OR_PROJECT, "저장소 등록·삭제", requires_step_up=True),
    ]
)

ALL = (REPO_VIEW, REPO_MANAGE)
