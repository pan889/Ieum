"""서버 에러 코드와 번역 카탈로그가 어긋나지 않게 고정한다.

서버는 code 만 주고 표시 문구는 클라이언트가 번역한다
(docs/architecture/i18n.md 1절). 그래서 서버가 새 코드를 내보내기 시작하면
카탈로그에도 같은 커밋에 들어가야 한다. 안 그러면 사용자는 번역 키를 날것으로
보게 된다. 그 순간을 여기서 잡는다.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

API_SRC = Path(__file__).resolve().parents[1] / "src" / "ieum"
CATALOG_DIR = Path(__file__).resolve().parents[3] / "packages" / "i18n"
LOCALES = ("en", "ko")


def _catalog(locale: str) -> dict[str, str]:
    path = CATALOG_DIR / locale / "errors.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _emitted_codes() -> set[str]:
    """소스에서 실제로 발생할 수 있는 에러 코드를 모은다.

    두 경로가 있다: 예외 클래스의 `code = "..."` 기본값과,
    호출부에서 넘기는 `code="..."` 키워드 인자.
    """
    codes: set[str] = set()
    for path in API_SRC.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # class Foo(IeumError): code = "..."
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "code"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        codes.add(node.value.value)
            # raise Foo(..., code="...")
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "code"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        codes.add(kw.value.value)
    # 내부용이라 사용자에게 보이지 않는 것들
    return {c for c in codes if "." in c and not c.startswith("request.")}


def test_catalog_files_exist() -> None:
    for locale in LOCALES:
        assert (CATALOG_DIR / locale / "errors.json").is_file()


@pytest.mark.parametrize("locale", LOCALES)
def test_every_emitted_code_has_a_translation(locale: str) -> None:
    catalog = _catalog(locale)
    missing = sorted(_emitted_codes() - set(catalog))
    assert not missing, (
        f"{locale}/errors.json 에 없는 에러 코드: {missing}\n"
        "서버가 코드를 내보내면 같은 커밋에 두 언어 모두 채워야 한다."
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_no_orphan_translations(locale: str) -> None:
    """소스에서 사라진 코드가 카탈로그에 남아 있으면 정리 대상이다."""
    catalog = _catalog(locale)
    emitted = _emitted_codes()
    # HTTP 핸들러가 매핑하는 코드는 예외 클래스에 없다 (errors.py 의 상태코드 표)
    handler_codes = {
        "common.http_error",
        "common.method_not_allowed",
        "internal.error",
    }
    orphans = sorted(set(catalog) - emitted - handler_codes)
    assert not orphans, f"{locale}/errors.json 에 쓰이지 않는 코드가 남아 있다: {orphans}"
