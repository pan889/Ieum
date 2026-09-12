"""공개 파일이 **비공개 저장소를 가리키지 않는가.**

`pan889/ieum-docs` 는 공개하지 않는다 — 개발의 핵심 내용과 의사결정 창구다
(개발 규칙 2절 15항). 그런데 이 저장소는 공개다. 여기서 그 주소를 적으면
읽는 사람에게는 **404 이고, 그건 안내가 아니라 막다른 길이다.**

이 시험이 있는 이유는 링크가 조용히 다시 생기기 때문이다. 실제로 그랬다 —
README 에 여섯 개, CHANGELOG 에 하나, 그리고 **SCIM 이 IdP 관리자에게
내보내는 `documentationUri`** 까지 그 주소였다. 마지막 것은 우리 화면에
안 보이므로 눈으로는 영원히 안 걸린다.

예외는 `CLAUDE.md` 하나다. 그 파일이 존재하는 이유가 "규칙 정본이 어디
있는지" 를 알리는 것이라서, 가리키지 않으면 파일이 뜻을 잃는다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: 이 조각이 들어 있으면 비공개 저장소를 가리키는 것이다.
NEEDLES = ("ieum-docs", "pan889.github.io")

#: 가리켜도 되는 유일한 자리.
ALLOWED = frozenset({"CLAUDE.md"})

#: 사람이 읽거나 앱이 내보내는 것만 본다. 잠금 파일·바이너리는 뺀다.
SUFFIXES = frozenset({".md", ".py", ".ts", ".tsx", ".js", ".mjs", ".yml", ".yaml", ".json", ".sh"})


def _tracked_files() -> list[Path]:
    """git 이 아는 파일만 본다 — node_modules 나 빌드 산출물을 훑지 않으려고.

    git 이 없으면 **건너뛰지 않고 실패한다.** 아무것도 안 보면서 초록이 되는
    시험은 없는 것보다 나쁘다.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    return [REPO / name for name in result.stdout.decode().split("\0") if name]


def test_there_is_something_to_look_at() -> None:
    """훑을 파일이 0개인데 통과하는 것을 막는다."""
    looked = [p for p in _tracked_files() if p.suffix in SUFFIXES]
    assert len(looked) > 100, f"훑은 파일이 {len(looked)}개뿐이다 — 이 시험이 아무것도 안 보고 있다"


def test_the_rule_would_catch_the_link_if_it_came_back() -> None:
    """검사식 자체가 도는지 본다. `CLAUDE.md` 에는 실제로 그 주소가 있다."""
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert any(needle in text for needle in NEEDLES), (
        "CLAUDE.md 가 문서 저장소를 안 가리킨다 — 그러면 이 시험의 검사식이 맞는지 확인할 길이 없다"
    )


def test_no_public_file_points_at_the_private_repo() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        if path.suffix not in SUFFIXES:
            continue
        relative = path.relative_to(REPO).as_posix()
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if any(needle in line for needle in NEEDLES):
                offenders.append(f"{relative}:{number}: {line.strip()[:100]}")

    assert offenders == [], "공개 파일이 비공개 저장소를 가리킨다:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("needle", NEEDLES)
def test_every_needle_is_a_real_address_fragment(needle: str) -> None:
    """오타 난 조각은 아무것도 안 잡으면서 잡는 척한다."""
    assert needle in "https://github.com/pan889/ieum-docs https://pan889.github.io/ieum-docs/"
