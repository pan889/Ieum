"""커밋 메시지에서 이슈를 찾는다 (A22, M6).

## 왜 순수 함수인가

사람이 손으로 쓴 글에서 뜻을 읽어내는 일이다. 틀리는 방향이 두 가지이고 둘 다
비싸다:

- **못 찾으면** 링크가 안 생기고, 사람은 연동이 안 된다고 여긴다.
- **잘못 찾으면** 남의 이슈에 남의 커밋이 붙는다. `ENG-12` 를 지운 뒤에도
  `ENG-120` 커밋이 그 자리에 남는 것이 이 종류다.

그래서 DB 없이 값으로 붙잡는다 (`test_vcs_refs.py`).

## 무엇을 이슈 키로 보는가

`PROJ-123`. 프로젝트 키는 대문자와 숫자(`org/models.py` 의 `key = upper(key)`
제약), 번호는 숫자다. **경계를 본다:**

- `ENG-12` 는 찾는다.
- `ENG-12.` `(ENG-12)` `[ENG-12]` 도 찾는다 — 사람이 문장에 넣어 쓴다.
- `ENG-121` 에서 `ENG-12` 를 찾지 **않는다.**
- `UTF-8` `SHA-1` `RFC-2119` 처럼 키가 아닌 것을 걸러야 한다. 이건 모양으로
  가릴 수 없다 — `UTF` 도 대문자다. **판정은 프로젝트 키 목록이 한다**(부르는
  쪽이 넘긴다). 모양만 보고 링크하면 커밋마다 유령 이슈가 붙는다.
- `feature/ENG-12-something` 같은 브랜치 이름 안에서도 찾는다.

## 닫는다는 말은 기록하되, 닫지는 않는다

`fixes ENG-12` 를 만나면 **그렇게 적혀 있다는 사실**을 남긴다. 상태를 실제로
옮기지는 않는다:

- 어느 전이로 옮길지는 프로젝트의 워크플로우마다 다르고, 맞는 전이가 없는
  워크플로우도 있다.
- 커밋은 되돌려진다(revert). 상태를 자동으로 옮겼다면 되돌릴 때 되돌아오지
  않는다 — 그러면 "닫혔다" 가 거짓이 된다.

그 자동화가 필요해지면 프로젝트별 전이 지도를 두는 일이고, 그건 이 슬라이스
밖이다. 지금은 화면이 "이 커밋이 닫는다고 적었다" 를 보여 준다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: 이슈 키 모양. 경계는 문자·숫자·하이픈이 아닌 것이어야 한다.
#:
#: 뒤쪽 `(?![0-9])` 가 `ENG-121` 에서 `ENG-12` 를 잘라 오는 것을 막는다.
#: 앞쪽 `(?<![A-Za-z0-9_-])` 는 `xENG-12` 와 `12-ENG-12` 를 막는다 —
#: 하이픈까지 막는 이유는 `SOME-ENG-12` 가 `ENG` 프로젝트의 것이 아니기
#: 때문이다.
_KEY = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]{1,15})-([0-9]{1,9})(?![0-9])")

#: "닫는다" 로 읽는 말. GitHub·GitLab 이 자기 이슈에 쓰는 낱말을 그대로 쓴다 —
#: 사람이 이미 그 습관을 갖고 있다.
CLOSING_WORDS = frozenset(
    {
        "close",
        "closes",
        "closed",
        "fix",
        "fixes",
        "fixed",
        "resolve",
        "resolves",
        "resolved",
    }
)

#: 키 바로 앞에서 낱말을 찾을 창. 낱말 하나와 공백·콜론 정도가 들어갈 만큼만
#: 본다 — 넓히면 두 문장 앞의 `fixes` 가 엉뚱한 키에 붙는다.
_LOOKBEHIND = 12


@dataclass(frozen=True, slots=True)
class Ref:
    """글에서 찾은 이슈 하나."""

    key: str
    #: 프로젝트 키. 판정에 쓴 값이라 대문자다.
    project_key: str
    number: int
    #: `fixes ENG-12` 처럼 닫는다고 적혀 있었나.
    closing: bool


def find_refs(text: str, *, known_projects: Iterable[str]) -> list[Ref]:
    """글에서 이슈 키를 찾는다. 같은 키가 여러 번 나오면 한 번만 준다.

    `known_projects` 에 없는 프로젝트 키는 **버린다.** `UTF-8` 이나 `SHA-1` 을
    이슈로 읽으면 커밋마다 유령 링크가 생기고, 목록을 못 믿게 된다.

    `closing` 은 **한 번이라도** 닫는다고 적혔으면 참이다. 같은 키가 본문에
    두 번 나오고 한쪽에만 `fixes` 가 붙는 경우가 흔하다(제목과 본문).
    """
    allowed = {key.upper() for key in known_projects}
    found: dict[str, Ref] = {}
    for match in _KEY.finditer(text):
        project_key = match.group(1).upper()
        if project_key not in allowed:
            continue
        key = f"{project_key}-{int(match.group(2))}"
        closing = _closing_before(text, match.start())
        seen = found.get(key)
        found[key] = Ref(
            key=key,
            project_key=project_key,
            number=int(match.group(2)),
            closing=closing or (seen.closing if seen else False),
        )
    return list(found.values())


def _closing_before(text: str, at: int) -> bool:
    """키 바로 앞에 닫는 낱말이 있나.

    창을 좁게 두는 이유: `fixes the build, touches ENG-12` 에서 `fixes` 가
    `ENG-12` 에 붙으면 안 된다. 낱말과 구분자 몇 자만 본다.
    """
    window = text[max(0, at - _LOOKBEHIND) : at]
    # 키 바로 앞은 구분자여야 한다. `fixesENG-12` 는 낱말이 아니다.
    if window and not window[-1].isspace() and window[-1] not in ":#-":
        return False
    words = re.findall(r"[A-Za-z]+", window)
    return bool(words) and words[-1].lower() in CLOSING_WORDS


__all__ = ["CLOSING_WORDS", "Ref", "find_refs"]
