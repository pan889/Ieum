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


def normalize_text(text: str) -> str:
    """공백을 하나로 접고 다듬는다. 줄바꿈·들여쓰기가 바뀌었다고 고아가 되면 안 된다."""
    return collapse(text).strip()


def collapse(text: str) -> str:
    """공백만 하나로 접는다. **다듬지 않는다.**

    앞뒤 문맥에서는 붙어 있던 공백이 단서다. `"배포 전 "` 의 끝 공백을 떼면
    인용 바로 앞 글자와 어긋나 문맥 비교가 헛돈다.
    """
    return _WHITESPACE.sub(" ", text)


def locate(haystack: str, anchor: Anchor) -> AnchorMatch | None:
    """평문 안에서 앵커 자리를 찾는다. 못 찾으면 None(고아).

    반환 위치는 **넘긴 문자열 기준**이다. 호출자가 평문을 넘겼으면 평문
    기준이고, 그게 화면에서 하이라이트할 때 쓰는 좌표계와 같다.
    """
    quote = normalize_text(anchor.exact)
    if not quote:
        return None
    text = normalize_text(haystack)
    if not text:
        return None

    exact = _find_exact(text, quote, anchor)
    if exact is not None:
        return exact
    return _find_fuzzy(text, quote)


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
    matcher = SequenceMatcher(None, text, quote, autojunk=False)
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
            score = _ratio(window, quote)
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
    "FUZZY_THRESHOLD",
    "MAX_CONTEXT",
    "MAX_QUOTE",
    "Anchor",
    "AnchorMatch",
    "build_anchor",
    "collapse",
    "locate",
    "normalize_text",
]
