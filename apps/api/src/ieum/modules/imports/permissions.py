"""imports 가 정의하는 권한.

## 미리 보기와 적재가 **같은 권한**이다

미리 보기는 아무것도 저장하지 않으니 더 낮게 둘 수도 있었다. 그러지 않은
이유는 미리 보기가 **어느 메일이 이 설치본에 있는지 알려 주기** 때문이다 —
사람 표에 짐작한 메일을 채운 묶음을 올리면 "이었다/못 이었다" 로 계정 존재가
그대로 새어 나온다. 권한을 나누면 그 통로가 낮은 문 뒤에 생긴다.

## step-up 을 요구한다

적재는 **남의 이름으로 수백 개를 만드는 일**이다(작성자·담당자가 우리 쪽
사람에 붙는다). 몰래 도는 일이 아니어야 한다.
"""

from __future__ import annotations

from ieum.core.permissions import PermissionDef, ScopeKind, registry

IMPORT_RUN = "imports.run"

registry.register_many(
    [
        PermissionDef(
            IMPORT_RUN,
            frozenset({ScopeKind.GLOBAL, ScopeKind.PROJECT}),
            "이관 묶음 미리 보기와 적재",
            requires_step_up=True,
        ),
    ]
)

ALL = (IMPORT_RUN,)
