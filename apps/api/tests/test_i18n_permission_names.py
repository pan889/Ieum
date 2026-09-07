"""권한 이름은 화면이 번역한다 — 두 언어 모두.

`permissions.py` 의 `description` 은 **한국어로 고정된 개발자용 메모**다.
한때 그것을 API 로 내보냈고, 역할 편집 화면이 그대로 그렸다 — 영어 화면에
한국어 권한 이름이 떴다(WebAuthn 자격증명 이름에서 같은 실수를 했다).

이제 서버는 키만 준다. 표시 이름은 `admin.json` 의 `permission.<키>` 다.
그래서 새 권한을 등록하면 **같은 커밋에** 두 언어를 채워야 한다. 안 그러면
사용자는 `permission.issue.foo` 를 날것으로 본다. 그 순간을 여기서 잡는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ieum.core.permissions import registry

CATALOG_DIR = Path(__file__).resolve().parents[3] / "packages" / "i18n"
LOCALES = ("en", "ko")
PREFIX = "permission."


@pytest.fixture(autouse=True, scope="module")
def _register_all() -> None:
    """레지스트리를 채운다.

    권한은 각 모듈의 `permissions.py` 가 import 될 때 등록된다. 이 파일만
    돌리면 그 모듈들이 안 불려서 레지스트리가 비고, 테스트는 **아무것도
    검사하지 않은 채 통과한다.**
    """
    from ieum.modules.identity import permissions as _identity  # noqa: F401
    from ieum.modules.issues import permissions as _issues  # noqa: F401
    from ieum.modules.notify import permissions as _notify  # noqa: F401
    from ieum.modules.org import permissions as _org  # noqa: F401
    from ieum.modules.wiki import permissions as _wiki  # noqa: F401


def _registered() -> set[str]:
    """실제 권한만. `test.` 로 시작하는 것은 `test_core_permissions.py` 가
    레지스트리 자체를 시험하려고 넣은 가짜다 — 전역 레지스트리에 남으므로
    (한 프로세스 안에서) 여기까지 흘러 들어온다. 접두사가 그 표식이고,
    `test_i18n_error_codes.py` 도 같은 방식으로 내부 코드를 걸러 낸다."""
    # `registry` 는 dict 가 아니다. `keys()` 를 먼저 받아 두면 ruff 의
    # dict 관용구 규칙(SIM118)과 부딪히지 않는다.
    keys = registry.keys()
    return {key for key in keys if not key.startswith("test.")}


def _names(locale: str) -> dict[str, str]:
    data = json.loads((CATALOG_DIR / locale / "admin.json").read_text(encoding="utf-8"))
    return {k.removeprefix(PREFIX): v for k, v in data.items() if k.startswith(PREFIX)}


def test_the_registry_is_not_empty() -> None:
    """비어 있으면 아래 단언이 전부 참이 된다 — 그건 검사가 아니다."""
    assert len(_registered()) > 20


@pytest.mark.parametrize("locale", LOCALES)
def test_every_permission_has_a_name(locale: str) -> None:
    missing = sorted(_registered() - set(_names(locale)))
    assert not missing, (
        f"{locale}/admin.json 에 없는 권한 이름: {missing}\n"
        "권한을 등록하면 같은 커밋에 두 언어 모두 채워야 한다."
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_no_orphan_names(locale: str) -> None:
    """사라진 권한의 이름이 남아 있으면 정리 대상이다."""
    orphans = sorted(set(_names(locale)) - _registered())
    assert not orphans, f"{locale}/admin.json 에 쓰이지 않는 권한 이름이 남아 있다: {orphans}"


def test_the_server_does_not_ship_display_copy() -> None:
    """응답 스키마에 설명 필드가 다시 생기면 화면이 그것을 그린다.

    `description` 은 파이썬에 남겨 둔다(정의 옆의 개발자용 메모다). 나가지
    않는 것만 고정한다.
    """
    from ieum.modules.org.schemas import PermissionDefResponse

    assert "description" not in PermissionDefResponse.model_fields
