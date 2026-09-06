"""런타임 의존성 누락 검사.

`src/ieum` 이 import 하는 서드파티가 **런타임** 의존성 목록에 있어야 한다.
dev 에만 있으면 개발 환경에서는 멀쩡하고 컨테이너에서만 죽는다 — 실제로
웹훅 전송이 그랬다(`httpx` 가 dev 에만 있어서 워커가 부팅에 실패했다).

정적 검사다. 설치된 메타데이터만 읽고 네트워크를 타지 않는다.
"""

from __future__ import annotations

import ast
import sys
from collections import deque
from importlib.metadata import PackageNotFoundError, distribution, packages_distributions
from pathlib import Path

from packaging.requirements import Requirement

SRC = Path(__file__).resolve().parents[1] / "src" / "ieum"
PROJECT = "ieum-api"


def _top_level_imports(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def _runtime_closure(project: str) -> set[str]:
    """런타임 의존성의 전이 폐포. `extra == "dev"` 로 걸린 것은 뺀다."""
    seen: set[str] = set()
    queue: deque[str] = deque([project])
    while queue:
        name = _normalize(queue.popleft())
        if name in seen:
            continue
        seen.add(name)
        try:
            requirements = distribution(name).requires or []
        except PackageNotFoundError:
            continue
        for raw in requirements:
            requirement = Requirement(raw)
            marker = requirement.marker
            # 마커가 extra 를 요구하면 옵션 의존이다. 런타임 폐포에 넣지 않는다.
            # (설치된 extra 는 여기서 알 수 없으므로 "extra" 가 언급되면 뺀다.)
            if marker is not None and "extra" in str(marker):
                continue
            queue.append(requirement.name)
    return seen


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def test_every_runtime_import_is_a_runtime_dependency() -> None:
    imported = _top_level_imports(SRC)
    mapping = packages_distributions()
    closure = _runtime_closure(PROJECT)

    missing: dict[str, list[str]] = {}
    for module in sorted(imported):
        if module == "ieum" or module in sys.stdlib_module_names:
            continue
        providers = mapping.get(module)
        if not providers:
            # 배포 메타데이터로 못 찾는 모듈. 여기서 판단하지 않는다.
            continue
        if not any(_normalize(p) in closure for p in providers):
            missing[module] = providers

    assert not missing, (
        f"런타임 의존성에 없는 import 다. dev 에만 두면 컨테이너에서만 죽는다: {missing}"
    )


def test_the_check_would_catch_a_dev_only_import() -> None:
    """검사가 실제로 무언가를 걸러 내는지. 통과만 하는 검사는 검사가 아니다."""
    closure = _runtime_closure(PROJECT)
    # 테스트 전용 의존성은 폐포에 없어야 한다.
    assert "moto" not in closure
    assert "pytest" not in closure
    # 런타임에 실제로 쓰는 것은 있어야 한다.
    assert "httpx" in closure
    assert "fastapi" in closure
