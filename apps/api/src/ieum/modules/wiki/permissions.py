"""wiki 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

SPACE = frozenset({ScopeKind.SPACE})
GLOBAL = frozenset({ScopeKind.GLOBAL})

PAGE_VIEW = "wiki.page.view"
PAGE_CREATE = "wiki.page.create"
PAGE_EDIT = "wiki.page.edit"
PAGE_DELETE = "wiki.page.delete"
PAGE_MOVE = "wiki.page.move"
PAGE_RESTRICT = "wiki.page.restrict"
COMMENT_ADD = "wiki.comment.add"
COMMENT_EDIT_OWN = "wiki.comment.edit_own"
COMMENT_EDIT_ANY = "wiki.comment.edit_any"
SPACE_ADMIN = "wiki.space.admin"
SPACE_CREATE = "wiki.space.create"

registry.register_many(
    [
        PermissionDef(PAGE_VIEW, SPACE, "문서 조회"),
        PermissionDef(PAGE_CREATE, SPACE, "문서 생성"),
        PermissionDef(PAGE_EDIT, SPACE, "문서 수정"),
        PermissionDef(PAGE_DELETE, SPACE, "문서 휴지통으로"),
        # 이동은 트리 전체를 옮긴다. 수정과 따로 준다 — 편집자에게 구조를
        # 바꿀 권한까지 자동으로 주면 실수 한 번이 넓게 퍼진다.
        PermissionDef(PAGE_MOVE, SPACE, "문서 이동·복사"),
        PermissionDef(PAGE_RESTRICT, SPACE, "문서 열람 제한 설정"),
        PermissionDef(COMMENT_ADD, SPACE, "문서 코멘트 작성"),
        PermissionDef(COMMENT_EDIT_OWN, SPACE, "자신의 문서 코멘트 수정"),
        PermissionDef(COMMENT_EDIT_ANY, SPACE, "타인의 문서 코멘트 수정"),
        PermissionDef(SPACE_ADMIN, SPACE, "스페이스 설정·권한 변경"),
        PermissionDef(SPACE_CREATE, GLOBAL, "스페이스 생성"),
    ]
)

ALL = (
    PAGE_VIEW,
    PAGE_CREATE,
    PAGE_EDIT,
    PAGE_DELETE,
    PAGE_MOVE,
    PAGE_RESTRICT,
    COMMENT_ADD,
    COMMENT_EDIT_OWN,
    COMMENT_EDIT_ANY,
    SPACE_ADMIN,
    SPACE_CREATE,
)
