"""판 번호가 여러 곳에 적혀 있고, **한 곳만 갱신되는 날이 온다.**

`apps/api/pyproject.toml` 이 정본이다. 나머지는 그것을 따라야 하는데, 파이썬이
아닌 것들(JS 패키지, Helm 차트)은 그 값을 읽을 방법이 없어서 손으로 적는다.
그래서 게이트가 본다 — conventions.md 의 "같은 값을 두 곳에 두면 한 곳만
갱신되는 날이 온다" 가 정확히 이 자리다.

어긋나면 무엇이 나빠지는가:

- `/openapi.json` 의 판이 틀리면 **웹훅을 받는 쪽과 API 를 부르는 스크립트가
  잘못된 판을 읽는다.** 호환을 판으로 판단하는 쪽에게는 거짓 정보다.
- 차트의 `appVersion` 이 틀리면 `kubectl` 로 보는 판과 실제가 다르다. 사고
  중에 그것을 보고 판단한다.
- 릴리스 워크플로가 태그와 이 값을 맞추므로, 어긋나면 **릴리스가 멈춘다.**
  그것이 이 게이트가 있는 다른 이유다: 멈추는 것을 여기서 먼저 알게 한다.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: 정본. 여기를 고치면 나머지가 따라가야 한다.
SOURCE = REPO / "apps" / "api" / "pyproject.toml"

#: `"version"` 을 그대로 들고 있는 JS 패키지들.
PACKAGES = (
    REPO / "apps" / "web" / "package.json",
    REPO / "packages" / "api-client" / "package.json",
    REPO / "packages" / "i18n" / "package.json",
)

CHART = REPO / "deploy" / "helm" / "ieum" / "Chart.yaml"


def app_version() -> str:
    return str(tomllib.loads(SOURCE.read_text())["project"]["version"])


def test_the_source_version_looks_like_a_version() -> None:
    parts = app_version().split(".")
    assert len(parts) == 3, f"MAJOR.MINOR.PATCH 가 아니다: {app_version()}"
    assert all(part.isdigit() for part in parts), f"숫자가 아닌 자리가 있다: {app_version()}"


@pytest.mark.parametrize("path", PACKAGES, ids=lambda p: p.parent.name)
def test_each_js_package_matches(path: Path) -> None:
    found = json.loads(path.read_text())["version"]
    assert found == app_version(), f"{path.relative_to(REPO)} 가 {found} 다"


def test_the_chart_app_version_matches() -> None:
    """`appVersion` 만 본다.

    차트의 `version` 은 **차트 자신의 판**이다 — 값을 안 바꾸고 템플릿만
    고치는 날이 있고, 그때 앱 판을 올리면 거짓말이 된다. 지금은 둘을 같이
    올리고 있지만, 게이트가 강제하는 것은 `appVersion` 하나다.
    """
    lines = [line for line in CHART.read_text().splitlines() if line.startswith("appVersion:")]
    assert len(lines) == 1, "Chart.yaml 에 appVersion 이 하나가 아니다"
    found = lines[0].split(":", 1)[1].strip().strip('"').strip("'")
    assert found == app_version(), f"Chart.yaml 의 appVersion 이 {found} 다"


def test_the_running_app_reports_the_same_version() -> None:
    """앱이 실제로 그 판을 말하는가.

    `main.py` 가 메타데이터를 읽으므로 보통 맞지만, 설치되지 않은 트리에서는
    `0.0.0` 으로 떨어진다 — 그 갈래가 CI 에서 조용히 켜져 있으면
    `/openapi.json` 이 판을 잃는다.
    """
    from ieum.main import APP_VERSION

    assert app_version() == APP_VERSION, (
        f"앱이 {APP_VERSION} 이라고 말한다 — 패키지가 설치되지 않았을 수 있다"
    )
