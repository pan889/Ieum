"""절대규칙 1의 강제 장치: 모듈 경계를 넘는 import 금지.

다른 모듈은 오직 `contracts` 를 통해서만 접근한다. ORM 모델·리포지토리·서비스를
직접 import 하면 여기서 실패한다.

ruff 의 banned-api 로는 이 규칙을 표현할 수 없다 — "같은 모듈 안에서는 허용"을
구분하지 못해 자기 내부 import 까지 막기 때문이다. 그래서 AST 로 직접 본다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODULES_DIR = Path(__file__).resolve().parents[1] / "src" / "ieum" / "modules"
#: 다른 모듈에서 import 해도 되는 이름
PUBLIC_SUBMODULES = {"contracts"}


def _module_names() -> list[str]:
    return sorted(
        d.name for d in MODULES_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")
    )


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """`ieum.modules.X.Y` 형태의 import 를 (전체경로, 줄번호) 로 모은다."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return [(m, ln) for m, ln in found if m.startswith("ieum.modules.")]


def _source_files() -> list[Path]:
    return sorted(p for p in MODULES_DIR.rglob("*.py") if "tests" not in p.parts)


def test_modules_directory_exists() -> None:
    assert MODULES_DIR.is_dir(), f"{MODULES_DIR} 가 없다"


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: str(p.name))
def test_no_cross_module_internal_imports(path: Path) -> None:
    known = set(_module_names())
    owning_module = next(
        (part for part in path.relative_to(MODULES_DIR).parts if part in known), None
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    violations: list[str] = []
    for imported, lineno in _imported_modules(tree):
        parts = imported.split(".")
        if len(parts) < 3:
            continue
        target_module = parts[2]
        if target_module == owning_module:
            continue  # 자기 모듈 내부는 자유
        submodule = parts[3] if len(parts) > 3 else None
        if submodule is not None and submodule not in PUBLIC_SUBMODULES:
            violations.append(
                f"{path.name}:{lineno} → {imported} (허용: ieum.modules.{target_module}.contracts)"
            )

    assert not violations, "모듈 경계 위반:\n  " + "\n  ".join(violations)
