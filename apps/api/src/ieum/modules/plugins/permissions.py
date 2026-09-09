"""plugins 가 정의하는 권한 상수."""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

GLOBAL = frozenset({ScopeKind.GLOBAL})

#: 등록된 앱 목록을 본다. 전역이다 — 앱은 설치 전체에 붙는다.
APP_VIEW = "plugins.app.view"
#: 앱을 등록하고 자리를 정하고 지운다. **전역 + step-up 이다.**
#:
#: step-up 인 이유가 이 모듈에서 가장 중요하다. 앱을 등록하는 것은
#: **화면 안에 남의 글을 놓는 자리를 내주는 일**이다. 토큰을 쥔 쪽이 이슈
#: 상세에 글을 붙일 수 있고, 사람은 그것을 우리가 쓴 것과 같은 자리에서
#: 읽는다. 세션을 훔친 사람이 조용히 앱 하나를 등록해 두면 그 뒤로 모든
#: 이슈에 그 글이 실린다 — 웹훅·저장소 등록과 같은 무게다.
APP_MANAGE = "plugins.app.manage"

registry.register_many(
    [
        PermissionDef(APP_VIEW, GLOBAL, "등록된 앱 목록 조회"),
        PermissionDef(APP_MANAGE, GLOBAL, "앱 등록·자리 지정·삭제", requires_step_up=True),
    ]
)

ALL = (APP_VIEW, APP_MANAGE)
