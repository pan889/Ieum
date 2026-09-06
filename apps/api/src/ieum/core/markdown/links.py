"""본문에서 내부 링크를 뽑는다 (wiki-markdown.md 5절).

```markdown
[배포 절차](page:ENG/deploy)   → 스페이스 키 + 경로
[IEUM-123](issue:IEUM-123)     → 이슈 키
```

정규식이 아니라 파서 토큰에서 뽑는다. 정규식은 코드 블록 안의 예시도
링크로 읽는다 — 문법을 설명한 문서가 실제로 그 이슈를 참조한 것이 된다.

**여기서는 해석하지 않는다.** 커널은 이슈 키가 무엇인지, 스페이스가 무엇인지
모른다(모듈 경계). 문자열만 돌려주고, 그것을 id 로 바꾸는 일은 그 엔티티를
아는 모듈이 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ieum.core.markdown.dialect import parser

ISSUE = "issue"
PAGE = "page"

#: 한 본문에서 인정하는 링크 수. 상한이 없으면 문서 하나가 링크 표를
#: 수천 줄로 만든다.
MAX_LINKS = 100


@dataclass(frozen=True, slots=True)
class LinkRef:
    """`issue:ENG-1` → `LinkRef("issue", "ENG-1")`."""

    scheme: str
    target: str


def extract_links(text: str, *, schemes: tuple[str, ...] = (ISSUE, PAGE)) -> list[LinkRef]:
    """등장 순서대로, 중복 없이."""
    prefixes = tuple(f"{s}:" for s in schemes)
    if not any(p in text for p in prefixes):
        return []

    found: list[LinkRef] = []
    seen: set[tuple[str, str]] = set()
    for token in parser().parse(text):
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type != "link_open":
                continue
            href = child.attrGet("href")
            if not isinstance(href, str):
                continue
            scheme, _, target = href.partition(":")
            target = target.strip().strip("/")
            if scheme not in schemes or not target:
                continue
            key = (scheme, target)
            if key in seen:
                continue
            seen.add(key)
            found.append(LinkRef(scheme=scheme, target=target))
            if len(found) >= MAX_LINKS:
                return found
    return found


__all__ = ["ISSUE", "MAX_LINKS", "PAGE", "LinkRef", "extract_links"]
