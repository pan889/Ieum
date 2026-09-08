"""등록된 권한은 **어떤 내장 역할에서든 닿을 수 있어야** 한다.

새 모듈을 만들면서 `seed.py` 의 Administrator 묶음에 그 모듈의 `ALL` 을
넣는 것을 빠뜨리면, 그 권한들은 정의만 있고 **아무도 가질 수 없다.** 손으로
역할을 만들어 넣을 수는 있지만, 그건 "새 기능이 켜지지 않는다" 로 먼저
드러난다 — 실제로 desk 를 추가한 직후 관리자가 `desk.portal.manage` 로
403 을 받았다.

같은 결의 결함을 이 세션에서 세 번 만났다: 권한 이름 게이트가 손으로 든
import 목록 때문에 새 모듈을 놓쳤고, `user.is_customer` 는 읽는 자리만 있고
켜는 자리가 없었고, 여기서는 시드가 새 권한을 모르고 있었다. 공통점은
**목록을 손으로 들고 있다는 것**이다. 목록은 그대로 두되(내장 역할의 구성은
사람이 정할 일이다), 빠진 것을 눈에 보이게 한다.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from ieum.core.permissions import registry
from ieum.seed import BUILTIN_ROLES

MODULES_DIR = Path(__file__).resolve().parents[1] / "src" / "ieum" / "modules"


@pytest.fixture(autouse=True, scope="module")
def _register_all() -> None:
    """레지스트리를 채운다. 목록을 손으로 들지 않고 디스크에서 찾는다."""
    for path in sorted(MODULES_DIR.glob("*/permissions.py")):
        importlib.import_module(f"ieum.modules.{path.parent.name}.permissions")


def _registered() -> set[str]:
    """실제 권한만. `test.` 은 `test_core_permissions.py` 가 넣은 가짜다."""
    keys = registry.keys()
    return {key for key in keys if not key.startswith("test.")}


def _granted() -> set[str]:
    return {grant for _, grants in BUILTIN_ROLES.values() for grant in grants}


def test_the_registry_is_not_empty() -> None:
    """비어 있으면 아래 단언이 참이 된다 — 그건 검사가 아니다."""
    assert len(_registered()) > 20


def test_every_permission_is_reachable_from_a_builtin_role() -> None:
    missing = sorted(_registered() - _granted())
    assert not missing, (
        "내장 역할 어디에도 없는 권한: "
        + ", ".join(missing)
        + "\n새 모듈을 추가하면 `seed.py` 의 Administrator 에 그 모듈의 `ALL` 을 넣는다."
    )


def test_no_builtin_role_grants_an_unknown_permission() -> None:
    """시드가 없는 권한을 주면 그 역할은 조용히 아무것도 안 준다.

    권한 상수를 지우거나 이름을 바꿀 때 이쪽을 함께 고치지 않으면 그렇게
    된다 — 화면은 역할이 있다고 말하고 평가에서는 무시된다.
    """
    unknown = sorted(_granted() - _registered())
    assert not unknown, f"등록되지 않은 권한을 주는 내장 역할이 있다: {unknown}"


def test_the_scope_of_every_grant_fits_the_role() -> None:
    """좁은 역할에 전역 전용 권한을 넣으면 저장은 되고 평가에서 조용히
    무시된다 — "줬는데 안 된다" 가 된다.

    **전역 역할은 예외다.** 전역 할당은 모든 스코프를 덮으므로 프로젝트
    스코프 권한을 담는 것이 정상이고, Administrator 가 바로 그 모양이다.
    이 시험을 처음 쓸 때 그 방향까지 잡았고, 그래서 `org` 서비스의 같은
    검사가 지나치게 엄격하다는 것이 드러났다 — 관리자가 역할 화면에서
    시드 자신과 같은 역할을 만들 수 없었다.
    """
    bad: list[str] = []
    for name, (scope_kind, grants) in BUILTIN_ROLES.items():
        if scope_kind == "global":
            continue
        for grant in grants:
            kinds = {kind.value for kind in registry.get(grant).scope_kinds}
            if scope_kind not in kinds:
                bad.append(f"{name}({scope_kind}) → {grant} (허용: {sorted(kinds)})")
    assert not bad, "스코프가 맞지 않는 내장 역할 권한:\n  " + "\n  ".join(bad)


def test_every_module_lists_its_own_permissions_in_all() -> None:
    """모듈의 `ALL` 은 **그 모듈이 등록한 것 전부**여야 한다.

    `ALL` 은 손으로 든 목록이다. 상수를 만들고 `register_many` 에는 넣었는데
    `ALL` 에 빠뜨리면 아무것도 터지지 않는다 — 시드는 상수를 직접 이름으로
    부르므로 위의 도달 가능성 시험도 통과한다. 대신 `ALL` 을 훑는 것들이
    조용히 그 권한을 빠뜨린다: `project_scoped()` 가 그렇고, 그러면 시험의
    "전권" 배우가 실은 그 권한이 없는 채로 돌아간다.

    빠졌다는 사실이 **주고 싶던 것을 안 주는 쪽**으로 나타나기 때문에 시험은
    초록으로 남는다. 그런 것은 세어 둔다.
    """
    bad: list[str] = []
    for path in sorted(MODULES_DIR.glob("*/permissions.py")):
        module = importlib.import_module(f"ieum.modules.{path.parent.name}.permissions")
        listed = set(getattr(module, "ALL", ()))
        for name, value in vars(module).items():
            if not name.isupper() or not isinstance(value, str):
                continue
            if value in _registered() and value not in listed:
                bad.append(f"{path.parent.name}.{name} ({value})")
    assert not bad, "모듈의 `ALL` 에 빠진 권한:\n  " + "\n  ".join(bad)
