"""notify 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

GLOBAL = frozenset({ScopeKind.GLOBAL})
GLOBAL_OR_PROJECT = frozenset({ScopeKind.GLOBAL, ScopeKind.PROJECT})

WEBHOOK_VIEW = "notify.webhook.view"
WEBHOOK_MANAGE = "notify.webhook.manage"

registry.register_many(
    [
        PermissionDef(WEBHOOK_VIEW, GLOBAL_OR_PROJECT, "웹훅과 전송 로그 조회"),
        # 웹훅은 데이터를 외부로 내보낸다. 새 엔드포인트를 몰래 등록하면
        # 조용한 유출 통로가 되므로 step-up 을 요구한다.
        PermissionDef(WEBHOOK_MANAGE, GLOBAL_OR_PROJECT, "웹훅 생성·수정", requires_step_up=True),
    ]
)

ALL = (WEBHOOK_VIEW, WEBHOOK_MANAGE)
