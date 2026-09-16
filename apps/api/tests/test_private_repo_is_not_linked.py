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
#:
#: **`pan889.github.io` 를 통째로 막을 수 없다.** 처음엔 그랬는데, 그때는 이
#: 계정의 github.io 가 비공개 문서 사이트 하나뿐이었다. 지금은 공개 매뉴얼이
#: `pan889.github.io/Ieum/` 에 있고 규칙 2절 15항이 README 더러 **그리로
#: 가리키라고** 한다. 그래서 조각을 비공개 쪽 경로까지 좁힌다 — 도메인이
#: 아니라 그 아래 어느 자리인지가 공개와 비공개를 가른다.
NEEDLES = ("ieum-docs", "pan889.github.io/ieum-docs")

#: 가리켜도 되는 자리.
#
#: - `CLAUDE.md` — 그 파일이 존재하는 이유가 "규칙 정본이 어디 있는지" 를
#:   알리는 것이다. 가리키지 않으면 파일이 뜻을 잃는다.
#: - 이 파일 자신 — 찾을 문자열을 들고 있어야 찾을 수 있다. **커밋하고 나서야
#:   붉어졌다**: `git ls-files` 가 추적 전에는 이 파일을 안 보여 줬다.
ALLOWED = frozenset({"CLAUDE.md", "apps/api/tests/test_private_repo_is_not_linked.py"})

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


def test_the_exemptions_still_point_at_real_files() -> None:
    """면제 목록이 **없는 파일**을 가리키면, 그 자리는 아무도 안 보는 채로 열린다.

    파일 이름이 바뀌었을 때 면제가 조용히 넓어지는 것을 막는다.
    """
    missing = sorted(name for name in ALLOWED if not (REPO / name).is_file())
    assert missing == [], f"면제 목록이 없는 파일을 가리킨다: {missing}"


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


def test_the_public_manual_is_not_mistaken_for_the_private_site() -> None:
    """**공개 매뉴얼 주소는 걸리면 안 된다.**

    둘이 같은 도메인에 있어서, 조각을 `pan889.github.io` 로 두면 규칙이
    가리키라고 한 자리를 이 시험이 막는다. 실제로 그렇게 됐었다 — 매뉴얼을
    내고 README 에 링크를 걸자마자 여기가 붉어졌다.
    """
    manual = "https://pan889.github.io/Ieum/"
    assert not any(needle in manual for needle in NEEDLES), (
        f"공개 매뉴얼 주소가 비공개 조각에 걸린다: {manual}"
    )
