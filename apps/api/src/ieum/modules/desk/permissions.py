"""desk 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

PROJECT = frozenset({ScopeKind.PROJECT})
GLOBAL = frozenset({ScopeKind.GLOBAL})

#: 포털과 요청 유형 정의. 프로젝트 단위다 — 포털이 프로젝트에 붙어 있다.
PORTAL_MANAGE = "desk.portal.manage"
#: 큐를 보고 티켓을 다룬다. data-model.md 의 권한 예시에 있는 이름이다.
QUEUE_WORK = "desk.queue.work"
#: 고객 조직 목록·소속 편집. 전역이다 — 조직은 프로젝트에 속하지 않는다.
CUSTOMER_MANAGE = "desk.customer.manage"

registry.register_many(
    [
        PermissionDef(PORTAL_MANAGE, PROJECT, "포털·요청 유형 정의 변경"),
        PermissionDef(QUEUE_WORK, PROJECT, "큐에서 티켓 처리"),
        # 고객 조직은 설치 전체에서 하나의 목록이고, 소속을 바꾸면 그 고객이
        # 보는 티켓의 범위가 바뀐다. 그래서 전역 + step-up 이다.
        PermissionDef(CUSTOMER_MANAGE, GLOBAL, "고객 조직·소속 관리", requires_step_up=True),
    ]
)

ALL = (PORTAL_MANAGE, QUEUE_WORK, CUSTOMER_MANAGE)

#: 상담원에게 기본으로 주는 묶음. 시드가 사용한다.
AGENT_DEFAULTS = (QUEUE_WORK,)
