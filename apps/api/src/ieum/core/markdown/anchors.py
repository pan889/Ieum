"""인라인 코멘트 앵커 (wiki-markdown.md 6절).

마크다운에는 노드 id 가 없다. 그래서 코멘트는 **인용한 텍스트**로 위치를
잡는다(W3C Web Annotation 의 TextQuoteSelector 방식). 문서가 고쳐지면 인용이
움직이거나 사라진다. 정확 일치 → 퍼지 매칭 → 그래도 없으면 **고아**다.

고아를 조용히 지우지 않는 것이 이 기능의 핵심이다. 코멘트가 소리 없이
사라지면 사람들은 코멘트를 믿지 않게 되고, 그러면 아무도 안 쓴다.

앵커는 **평문**에서 잡는다. 사용자는 렌더된 글을 드래그해서 코멘트를 달지,
`**굵게**` 같은 원문을 고르지 않는다. 마크다운 원문에 맞추면 서식이 조금만
바뀌어도(굵게를 기울임으로) 멀쩡한 인용이 고아가 된다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

#: 인용문 상한. 문단을 통째로 인용하면 앵커가 아니라 복사본이다.
MAX_QUOTE = 1000
#: 앞뒤 문맥. 같은 문장이 여러 번 나올 때 어느 것인지 가른다.
MAX_CONTEXT = 100
CONTEXT_SIZE = 40

#: 퍼지 매칭 기준. 낮추면 엉뚱한 곳에 붙고, 높이면 오타 하나에 고아가 된다.
FUZZY_THRESHOLD = 0.75

#: 한 번의 `relocate_all` 이 퍼지 탐색에 쓸 수 있는 총 글자 수 — 훑을 문서
#: 길이의 합이다.
#:
#: 퍼지 한 건의 비용은 문서 길이에 거의 비례한다(40만 자 문서에서 2.5초를
#: 쟀다). 그래서 **건수가 아니라 글자 수**로 잡는다: 보통 크기의 문서에서는
#: 수십 건이 되고, 아주 긴 문서에서는 한두 건이 된다. 어느 쪽이든 한 요청이
#: 퍼지에 쓰는 시간은 2초 언저리에서 멈춘다.
FUZZY_BUDGET_CHARS = 300_000

#: 창을 좌우로 밀어 보는 폭, 그리고 늘였다 줄여 보는 폭. 편집은 위치와
#: 길이를 함께 바꾼다 — 자리만 밀어 보면 찾은 글자가 어중간하게 잘린다.
_SHIFTS = (0, -8, 8, -20, 20, -40, 40)
_STRETCHES = (0, 4, -4, 12, -12, 30)

_WHITESPACE = re.compile(r"\s+")

How = Literal["exact", "fuzzy"]


@dataclass(frozen=True, slots=True)
class Anchor:
    """어디를 가리키는지. `version_number` 는 달 때의 판이다(진단용)."""

    exact: str
    prefix: str = ""
    suffix: str = ""
    #: 같은 인용이 여러 번 나올 때 몇 번째인지. 1부터.
    occurrence: int = 1
    version_number: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "exact": self.exact,
            "prefix": self.prefix,
            "suffix": self.suffix,
            "occurrence": self.occurrence,
            "version_number": self.version_number,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Anchor:
        occurrence = raw.get("occurrence", 1)
        version = raw.get("version_number")
        return Anchor(
            exact=str(raw.get("exact", ""))[:MAX_QUOTE],
            prefix=str(raw.get("prefix", ""))[:MAX_CONTEXT],
            suffix=str(raw.get("suffix", ""))[:MAX_CONTEXT],
            occurrence=occurrence if isinstance(occurrence, int) and occurrence >= 1 else 1,
            version_number=version if isinstance(version, int) else None,
        )


@dataclass(frozen=True, slots=True)
class AnchorMatch:
    start: int
    end: int
    how: How
    #: 0~1. 정확 일치는 1.0.
    score: float
    #: 지금 문서에 실제로 있는 글자. 퍼지로 붙었으면 인용문과 다를 수 있다.
    found: str


@dataclass(frozen=True, slots=True)
class Relocation:
    """`relocate_all` 의 한 건.

    `decided=False` 는 "못 찾았다" 가 아니라 **안 찾아봤다**는 뜻이다(퍼지 예산이
    떨어졌다). 둘을 가르는 이유는 하나다 — 안 찾아본 것을 고아로 적으면, 본문에
    멀쩡히 살아 있는 코멘트가 큰 문서라는 이유만으로 고아 표시를 단다.
    """

    match: AnchorMatch | None
    decided: bool


def normalize_text(text: str) -> str:
    """공백을 하나로 접고 다듬는다. 줄바꿈·들여쓰기가 바뀌었다고 고아가 되면 안 된다."""
    return collapse(text).strip()


def collapse(text: str) -> str:
    """공백만 하나로 접는다. **다듬지 않는다.**

    앞뒤 문맥에서는 붙어 있던 공백이 단서다. `"배포 전 "` 의 끝 공백을 떼면
    인용 바로 앞 글자와 어긋나 문맥 비교가 헛돈다.
    """
    return _WHITESPACE.sub(" ", text)


def locate(haystack: str, anchor: Anchor, *, fuzzy: bool = True) -> AnchorMatch | None:
    """평문 안에서 앵커 자리를 찾는다. 못 찾으면 None(고아).

    반환 위치는 **넘긴 문자열 기준**이다. 호출자가 평문을 넘겼으면 평문
    기준이고, 그게 화면에서 하이라이트할 때 쓰는 좌표계와 같다.

    `fuzzy=False` 는 **그대로 있는 것만** 찾는다(문자열 탐색 한 번). 퍼지
    탐색은 `difflib` 라 문서 길이에 비례해 초 단위로 걸리므로, 부르는 쪽이
    "여기서는 비싼 쪽을 안 쓴다" 를 말할 수 있어야 한다 — 한 요청에서 몇
    건까지 할지 고르는 자리가 있다(`PageCommentService.list_for`).
    """
    quote = normalize_text(anchor.exact)
    if not quote:
        return None
    text = normalize_text(haystack)
    if not text:
        return None
    return _locate(text, quote, anchor, fuzzy=fuzzy)


def _locate(text: str, quote: str, anchor: Anchor, *, fuzzy: bool) -> AnchorMatch | None:
    """다듬기가 끝난 뒤. 여러 건을 한 문서에 붙일 때 이 자리부터 다시 쓴다."""
    exact = _find_exact(text, quote, anchor)
    if exact is not None:
        return exact
    return _find_fuzzy(text, quote) if fuzzy else None


def relocate_all(
    haystack: str,
    anchors: Sequence[dict[str, Any] | None],
    *,
    budget_chars: int = FUZZY_BUDGET_CHARS,
) -> list[Relocation]:
    """앵커 여럿을 한 문서에 다시 붙인다. **동기 함수다** — 스레드에서 돌린다.

    한 건씩 `locate` 를 부르는 것과 두 가지가 다르다.

    1. 평문을 **한 번만** 다듬는다. 28만 자 문서에서 한 번에 14ms 이고,
       코멘트가 백 개면 그것만 1.4초다.
    2. 퍼지 탐색에 **총량 상한**이 있다. 상한을 넘긴 건은 건너뛰고
       `decided=False` 로 표시한다.

    그래서 정확 일치를 먼저 한 바퀴 다 돈다 — 예산은 정말 퍼지가 필요한
    건에만 쓰이고, 코드에서도 "싼 바퀴, 그다음 비싼 바퀴" 로 읽힌다.

    비어 있는 앵커(`None`)와 빈 인용은 **결론이 난 것**으로 친다 — 붙을 자리가
    없는 게 아니라 애초에 붙을 데를 안 가리킨다.
    """
    text = normalize_text(haystack)
    found: list[Relocation] = []
    #: 정확 일치로 못 찾은 것들. (자리, 다듬은 인용)
    retry: list[tuple[int, str]] = []

    for raw in anchors:
        if raw is None:
            found.append(Relocation(match=None, decided=True))
            continue
        anchor = Anchor.from_json(raw)
        quote = normalize_text(anchor.exact)
        if not quote or not text:
            found.append(Relocation(match=None, decided=True))
            continue
        exact = _find_exact(text, quote, anchor)
        if exact is not None:
            found.append(Relocation(match=exact, decided=True))
            continue
        found.append(Relocation(match=None, decided=False))
        retry.append((len(found) - 1, quote))

    spent = 0
    for at, quote in retry:
        # 첫 건은 예산을 넘겨도 해 본다. 40만 자짜리 문서에서 한 건도 안 하면
        # 그 문서의 고아는 본문이 되돌아와도 영영 안 붙는다.
        if spent and spent + len(text) > budget_chars:
            break
        spent += len(text)
        found[at] = Relocation(match=_find_fuzzy(text, quote), decided=True)
    return found


def _find_exact(text: str, quote: str, anchor: Anchor) -> AnchorMatch | None:
    starts = _all_starts(text, quote)
    if not starts:
        return None
    # 여럿이면 앞뒤 문맥으로 고르고, 그래도 못 가르면 몇 번째인지로 고른다.
    start = starts[0] if len(starts) == 1 else _best_by_context(text, starts, len(quote), anchor)
    return AnchorMatch(start=start, end=start + len(quote), how="exact", score=1.0, found=quote)


def _all_starts(text: str, quote: str) -> list[int]:
    starts: list[int] = []
    at = text.find(quote)
    while at != -1:
        starts.append(at)
        at = text.find(quote, at + 1)
    return starts


def _best_by_context(text: str, starts: list[int], length: int, anchor: Anchor) -> int:
    prefix, suffix = collapse(anchor.prefix), collapse(anchor.suffix)
    if not prefix and not suffix:
        # 문맥이 없으면 순번이 유일한 단서다. 범위를 벗어나면 첫 번째.
        index = anchor.occurrence - 1
        return starts[index] if 0 <= index < len(starts) else starts[0]

    def score(start: int) -> float:
        before = text[max(0, start - len(prefix)) : start] if prefix else ""
        after = text[start + length : start + length + len(suffix)] if suffix else ""
        total = 0.0
        if prefix:
            total += _ratio(before, prefix)
        if suffix:
            total += _ratio(after, suffix)
        return total

    return max(starts, key=score)


def _find_fuzzy(text: str, quote: str) -> AnchorMatch | None:
    """편집으로 조금 달라진 자리를 찾는다.

    모든 위치에 창을 대고 비교하면 문서 길이에 인용 길이를 곱한 만큼이 되어 큰 문서에서
    한 요청이 오래 걸린다. 대신 **가장 긴 공통 부분**으로 후보 지점을 하나
    잡고, 그 둘레만 몇 번 밀어 본다.
    """
    # 비교 대상(`quote`)을 고정해 두고 창만 갈아 끼운다. `SequenceMatcher` 는
    # 두 번째 열의 색인을 안에 들고 있어서, 새로 만들 때마다 인용을 다시 훑는다.
    matcher = SequenceMatcher(None, "", quote, autojunk=False)
    matcher.set_seq1(text)
    seed = matcher.find_longest_match(0, len(text), 0, len(quote))
    if seed.size == 0:
        return None

    best: AnchorMatch | None = None
    origin = seed.a - seed.b
    for shift in _SHIFTS:
        start = max(0, min(len(text), origin + shift))
        for stretch in _STRETCHES:
            end = min(len(text), start + len(quote) + stretch)
            if end <= start:
                continue
            window = text[start:end]
            matcher.set_seq1(window)
            # `ratio()` 는 두 길이를 곱한 만큼 걸린다(1000자 인용이면 창 하나에
            # 백만 번). `quick_ratio()` 들은 **상한**이라 이 밑이면 실제 점수도
            # 그 밑이다 — 그래서 여기서 거르는 것은 값을 바꾸지 않고 일만 줄인다.
            # 창 42개를 다 재던 것이 1.6초에서 0.07초가 됐다(10만 자 기준).
            bound = best.score if best is not None else FUZZY_THRESHOLD
            if matcher.real_quick_ratio() < bound or matcher.quick_ratio() < bound:
                continue
            score = matcher.ratio()
            if score >= FUZZY_THRESHOLD and (best is None or score > best.score):
                best = AnchorMatch(start=start, end=end, how="fuzzy", score=score, found=window)
    return best


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def build_anchor(
    haystack: str, start: int, end: int, *, version_number: int | None = None
) -> Anchor:
    """평문의 한 구간에서 앵커를 만든다. 테스트와 서버 쪽 도구용."""
    text = normalize_text(haystack)
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    quote = text[start:end][:MAX_QUOTE]
    return Anchor(
        exact=quote,
        prefix=text[max(0, start - CONTEXT_SIZE) : start],
        suffix=text[end : end + CONTEXT_SIZE],
        occurrence=len(_all_starts(text[:start], quote)) + 1 if quote else 1,
        version_number=version_number,
    )


__all__ = [
    "CONTEXT_SIZE",
    "FUZZY_BUDGET_CHARS",
    "FUZZY_THRESHOLD",
    "MAX_CONTEXT",
    "MAX_QUOTE",
    "Anchor",
    "AnchorMatch",
    "Relocation",
    "build_anchor",
    "collapse",
    "locate",
    "normalize_text",
    "relocate_all",
]
