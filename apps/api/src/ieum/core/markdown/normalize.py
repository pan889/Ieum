"""마크다운 정규화. 서버가 단독으로 소유한다 (wiki-markdown.md 7절).

에디터·API·임포터가 모두 이 함수를 지난다. 정규화가 없으면 에디터를 한 번
왕복할 때마다 diff 가 오염돼 버전 비교가 쓸모없어진다.

멱등성이 계약이다: `normalize(normalize(x)) == normalize(x)`.

끝의 개행은 떼고 돌려준다. 마크다운 **파일**이라면 붙어 있는 게 맞지만,
여기 결과는 DB 컬럼에 들어가는 본문이라 코멘트 한 줄마다 보이지 않는
개행이 따라붙는다. 파일로 내보낼 때 붙이면 된다.
"""

from __future__ import annotations

import mdformat

from ieum.core.exceptions import ValidationError

#: mdformat 확장. 방언(dialect.py)이 켠 기능과 짝이 맞아야 한다.
_EXTENSIONS = frozenset({"gfm", "frontmatter", "footnote"})

#: 본문 상한. 정규화기와 파서는 입력 길이에 선형이지만, 무한정 받으면
#: 한 요청이 워커를 오래 잡는다.
MAX_LENGTH = 200_000


def normalize(text: str) -> str:
    """저장 직전에 부른다. 실패하면 원문을 그대로 돌려주지 않고 거절한다.

    조용히 원문을 통과시키면 정규화되지 않은 본문이 DB 에 섞여 들어가
    멱등성 계약이 깨진다.
    """
    if len(text) > MAX_LENGTH:
        raise ValidationError(
            f"본문은 {MAX_LENGTH}자 이하여야 한다.",
            code="common.payload_too_large",
            details={"max": MAX_LENGTH, "length": len(text)},
        )
    if not text.strip():
        return ""
    try:
        return mdformat.text(text, extensions=set(_EXTENSIONS)).rstrip("\n")
    # 파서 내부 오류를 사용자에게 보여줄 수 있는 오류로 바꾼다.
    except Exception as exc:
        raise ValidationError(
            "마크다운을 해석할 수 없다.",
            code="common.invalid_markdown",
            details={"reason": str(exc)[:200]},
        ) from exc


__all__ = ["MAX_LENGTH", "normalize"]
