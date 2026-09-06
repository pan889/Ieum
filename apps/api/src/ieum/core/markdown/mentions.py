"""본문에서 멘션을 뽑는다.

멘션은 `[@Alice](user:<uuid>)` 로 저장한다. 표시 이름이 아니라 id 를 담으므로
사용자가 이름을 바꿔도 멘션이 안 깨진다.

정규식이 아니라 파서 토큰에서 뽑는다. 정규식은 코드 블록 안의 텍스트도
멘션으로 읽는다 — 문서에 예시를 적었을 뿐인데 알림이 나간다.
"""

from __future__ import annotations

from uuid import UUID

from ieum.core.markdown.dialect import parser

_SCHEME = "user:"

#: 한 본문에서 인정하는 멘션 수. 넘으면 앞에서부터 자른다. 수신자마다
#: 권한 검사가 한 번씩 도므로 상한이 없으면 코멘트 하나가 DB 를 오래 잡는다.
MAX_MENTIONS = 20


def extract_mentions(text: str) -> list[UUID]:
    """등장 순서대로, 중복 없이."""
    if _SCHEME not in text:
        return []

    found: list[UUID] = []
    seen: set[UUID] = set()
    for token in parser().parse(text):
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type != "link_open":
                continue
            href = child.attrGet("href")
            if not isinstance(href, str) or not href.startswith(_SCHEME):
                continue
            try:
                user_id = UUID(href[len(_SCHEME) :])
            except ValueError:
                # 사람이 손으로 쓴 `user:bob` 같은 것. 링크로는 남기고 멘션은 아니다.
                continue
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)
                if len(found) >= MAX_MENTIONS:
                    return found
    return found


__all__ = ["MAX_MENTIONS", "extract_mentions"]
