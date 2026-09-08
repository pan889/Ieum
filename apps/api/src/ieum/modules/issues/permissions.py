"""issues 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

PROJECT = frozenset({ScopeKind.PROJECT})
GLOBAL = frozenset({ScopeKind.GLOBAL})

ISSUE_VIEW = "issue.view"
ISSUE_CREATE = "issue.create"
ISSUE_EDIT = "issue.edit"
ISSUE_EDIT_OWN = "issue.edit_own"
ISSUE_TRANSITION = "issue.transition"
ISSUE_ASSIGN = "issue.assign"
ISSUE_DELETE = "issue.delete"
ISSUE_LINK = "issue.link"
COMMENT_ADD = "issue.comment.add"
COMMENT_EDIT_OWN = "issue.comment.edit_own"
COMMENT_EDIT_ANY = "issue.comment.edit_any"
COMMENT_VIEW_INTERNAL = "issue.comment.view_internal"
WORKLOG_ADD = "issue.worklog.add"
WORKLOG_EDIT_ANY = "issue.worklog.edit_any"
SECURITY_LEVEL_SET = "issue.security_level.set"
BOARD_MANAGE = "issue.board.manage"
#: 스프린트를 만들고 시작하고 닫는다 (M5). **보드와 같은 손잡이가 아니다.**
#:
#: 보드는 보는 방식이고, 스프린트는 **약속**이다 — 시작하면 그 기간의
#: 번다운이 찍히기 시작하고, 닫으면 남은 것이 어디론가 옮겨진다. 컬럼
#: 하나 고치는 것과 같은 무게로 두지 않는다.
SPRINT_MANAGE = "issue.sprint.manage"
WORKFLOW_MANAGE = "issue.workflow.manage"
FIELD_MANAGE = "issue.field.manage"

registry.register_many(
    [
        PermissionDef(ISSUE_VIEW, PROJECT, "이슈 조회"),
        PermissionDef(ISSUE_CREATE, PROJECT, "이슈 생성"),
        PermissionDef(ISSUE_EDIT, PROJECT, "모든 이슈 수정"),
        PermissionDef(ISSUE_EDIT_OWN, PROJECT, "자신이 보고한 이슈 수정"),
        PermissionDef(ISSUE_TRANSITION, PROJECT, "상태 전이"),
        PermissionDef(ISSUE_ASSIGN, PROJECT, "담당자 지정"),
        PermissionDef(ISSUE_DELETE, PROJECT, "이슈 아카이브"),
        PermissionDef(ISSUE_LINK, PROJECT, "이슈 관계 편집"),
        PermissionDef(COMMENT_ADD, PROJECT, "코멘트 작성"),
        PermissionDef(COMMENT_EDIT_OWN, PROJECT, "자신의 코멘트 수정"),
        PermissionDef(COMMENT_EDIT_ANY, PROJECT, "타인의 코멘트 수정"),
        # 내부 노트는 고객에게 절대 노출하지 않는다 (auth.md 5절).
        PermissionDef(COMMENT_VIEW_INTERNAL, PROJECT, "내부 노트 조회"),
        PermissionDef(WORKLOG_ADD, PROJECT, "작업 로그 기록"),
        PermissionDef(WORKLOG_EDIT_ANY, PROJECT, "타인의 작업 로그 수정"),
        PermissionDef(SECURITY_LEVEL_SET, PROJECT, "이슈 보안 레벨 지정"),
        PermissionDef(BOARD_MANAGE, PROJECT, "보드 정의 편집"),
        PermissionDef(SPRINT_MANAGE, PROJECT, "스프린트 관리"),
        # 워크플로우·필드 정의 변경은 프로젝트 전체 동작을 바꾼다.
        PermissionDef(WORKFLOW_MANAGE, GLOBAL, "워크플로우 정의 변경", requires_step_up=True),
        PermissionDef(FIELD_MANAGE, GLOBAL, "커스텀 필드 정의 변경", requires_step_up=True),
    ]
)

ALL = (
    ISSUE_VIEW,
    ISSUE_CREATE,
    ISSUE_EDIT,
    ISSUE_EDIT_OWN,
    ISSUE_TRANSITION,
    ISSUE_ASSIGN,
    ISSUE_DELETE,
    ISSUE_LINK,
    COMMENT_ADD,
    COMMENT_EDIT_OWN,
    COMMENT_EDIT_ANY,
    COMMENT_VIEW_INTERNAL,
    WORKLOG_ADD,
    WORKLOG_EDIT_ANY,
    SECURITY_LEVEL_SET,
    BOARD_MANAGE,
    SPRINT_MANAGE,
    WORKFLOW_MANAGE,
    FIELD_MANAGE,
)

#: 이슈를 다루는 사람에게 기본으로 주는 묶음. 시드가 사용한다.
MEMBER_DEFAULTS = (
    ISSUE_VIEW,
    ISSUE_CREATE,
    ISSUE_EDIT_OWN,
    ISSUE_TRANSITION,
    ISSUE_LINK,
    COMMENT_ADD,
    COMMENT_EDIT_OWN,
    WORKLOG_ADD,
)


def project_scoped() -> tuple[str, ...]:
    """프로젝트 스코프에 줄 수 있는 것만.

    **자리로 세지 않는다.** 시험 여러 곳이 `ALL[:-2]` 로 "마지막 둘은 전역"
    을 가정하고 있었는데, 그건 목록 가운데에 권한을 하나 넣는 순간 조용히
    틀린다 — 전역 권한을 프로젝트 스코프로 주려다 터지거나, 더 나쁘게는
    주고 싶던 것을 안 주게 된다. 등록된 정의에 물어본다.
    """
    return tuple(key for key in ALL if ScopeKind.PROJECT in registry.get(key).scope_kinds)
