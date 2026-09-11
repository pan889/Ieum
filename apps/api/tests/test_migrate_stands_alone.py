"""`ieum.migrate` 는 혼자 돌 수 있어야 한다.

어댑터는 **관리자의 기계에서** 돈다(ADR-0016). 거기에는 Ieum 서버도, 우리가
쓰는 서드파티도 깔려 있지 않다 — 관리자는 소스 시스템 옆에서 파이썬 하나로
그것을 돌리고 나온 파일을 우리에게 올린다.

그 전제는 임포트 한 줄로 조용히 깨진다. `from ieum.core.markdown import ...`
한 줄이면 그 순간 어댑터는 서버 없이는 못 도는 물건이 되고, 그 사실은 **고객이
자기 기계에서 돌려 보는 날** 처음 드러난다.

그래서 사람의 주의에 맡기지 않고 여기서 막는다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

MIGRATE = Path(__file__).resolve().parents[1] / "src" / "ieum" / "migrate"

#: 이 꾸러미가 기대도 되는 것. 표준 라이브러리와 자기 자신뿐이다.
ALLOWED_PREFIX = "ieum.migrate"


def _modules() -> list[Path]:
    return sorted(MIGRATE.rglob("*.py"))


def _imported_roots(tree: ast.AST) -> set[str]:
    """이 파일이 기대는 최상위 꾸러미 이름들."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` 는 자기 안이다.
            if node.level:
                continue
            if node.module:
                roots.add(node.module)
    return roots


def test_there_is_something_to_check() -> None:
    """시험이 **빈 목록을 훑고 초록이 되는 것**을 막는다.

    파일이 옮겨 가면 이 시험은 아무것도 안 보면서 계속 통과한다. 그것이
    이 저장소가 게이트에서 이미 겪은 고장이다.
    """
    assert len(_modules()) >= 2, f"{MIGRATE} 에 검사할 파일이 없다"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_it_only_leans_on_the_standard_library(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for name in sorted(_imported_roots(tree)):
        root = name.split(".")[0]
        if name.startswith(ALLOWED_PREFIX):
            continue
        assert root in sys.stdlib_module_names, (
            f"{path.name} 이 `{name}` 을 부른다 — "
            "이 꾸러미는 관리자의 기계에서 표준 라이브러리만으로 돌아야 한다"
        )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_it_does_not_reach_into_the_app(path: Path) -> None:
    """`ieum.*` 의 다른 무엇도 부르지 않는다.

    위 시험과 겹쳐 보이지만 다르다 — `ieum` 이 표준 라이브러리에 없으니 위에서도
    걸리기는 한다. 다만 **이 규칙이 왜 있는지**를 실패 메시지가 말해야 해서
    따로 둔다. 어느 날 `ieum` 이 어떤 경로로 허용되면 위 시험만 조용해진다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for name in sorted(_imported_roots(tree)):
        if name == "ieum" or (name.startswith("ieum.") and not name.startswith(ALLOWED_PREFIX)):
            pytest.fail(
                f"{path.name} 이 `{name}` 을 부른다 — 어댑터가 서버 없이는 "
                "못 도는 물건이 된다(ADR-0016)"
            )
