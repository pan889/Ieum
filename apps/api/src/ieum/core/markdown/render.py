"""마크다운 → HTML, 마크다운 → 평문.

HTML 은 메일과 내보내기용이다. 화면은 클라이언트가 같은 방언으로 직접
그린다 (wiki-markdown.md 1절). 원시 HTML 은 방언에서 꺼 뒀으므로 별도
새니타이저를 두지 않는다 — 새니타이저를 두면 "그럼 html 을 켜도 되겠네"
로 이어진다.
"""

from __future__ import annotations

from markdown_it.token import Token

from ieum.core.markdown.dialect import parser

#: 평문 추출에서 통째로 버리는 블록. 검색 색인이 코드로 오염되지 않게 한다
#: (wiki-markdown.md 10절 — 코드는 별도 필드로 색인한다).
_SKIP_BLOCKS = frozenset({"fence", "code_block", "front_matter"})


def to_html(text: str) -> str:
    rendered: str = parser().render(text)
    return rendered


def to_plaintext(text: str, *, include_code: bool = False) -> str:
    """검색 색인·발췌·알림 미리보기에 쓰는 평문.

    렌더 결과가 아니라 토큰에서 뽑는다. HTML 을 만들었다가 태그를 지우면
    표 셀이 붙어 버리고 링크 URI 가 본문에 섞인다.

    코드 블록은 기본으로 뺀다 — 색인이 코드로 오염되면 안 된다
    (wiki-markdown.md 10절: 코드는 별도 필드로 색인). 다만 **인라인 코멘트
    앵커**는 코드에도 달 수 있어야 하므로 그쪽은 `include_code=True` 로
    부른다. 코드에 단 코멘트가 항상 고아가 되면 코드 리뷰를 못 한다.
    """
    tokens = parser().parse(text)
    pieces: list[str] = []
    for token in tokens:
        if token.type in _SKIP_BLOCKS:
            if include_code and token.type in {"fence", "code_block"}:
                pieces.append(token.content + "\n")
            continue
        if token.type == "colon_fence":
            # 강조 상자 안쪽도 산문이다. 빼면 검색에서 안 잡힌다.
            pieces.append(to_plaintext(token.content, include_code=include_code) + "\n")
        elif token.type == "inline":
            pieces.append(_inline_text(token))
        elif token.type in {"paragraph_close", "heading_close", "list_item_close"}:
            pieces.append("\n")
        elif token.type in {"th_close", "td_close"}:
            # 표는 셀 텍스트만. 안 끊으면 "12" 처럼 붙어 색인이 망가진다.
            pieces.append(" ")
    return " ".join("".join(pieces).split())


def _inline_text(token: Token) -> str:
    if not token.children:
        return token.content
    out: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            out.append(child.content)
        elif child.type == "softbreak" or child.type == "hardbreak":
            out.append(" ")
    return "".join(out)


def excerpt(text: str, limit: int = 200) -> str:
    """알림·목록 미리보기용 앞부분."""
    plain = to_plaintext(text)
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + "…"


__all__ = ["excerpt", "to_html", "to_plaintext"]
