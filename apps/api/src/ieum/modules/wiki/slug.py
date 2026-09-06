"""제목 → slug.

slug 은 URL 과 `page:KEY/path` 링크에 들어간다. 사람이 읽을 수 있어야 하고,
같은 제목이면 같은 결과가 나와야 한다.

한글을 로마자로 옮기지 않는다. 옮기면 원래 제목을 되짚을 수 없고, 표기법마다
결과가 달라진다. 대신 그대로 둔다 — URL 인코딩은 브라우저가 알아서 하고,
주소창에서는 다시 한글로 보인다.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_LENGTH = 200

#: slug 에서 뺄 것. 경로 구분자(`/`)와 URL 에서 의미를 갖는 문자들.
_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"[\s_]+")
_DASHES = re.compile(r"-{2,}")


def slugify(title: str) -> str:
    """제목에서 slug 을 만든다. 비면 `untitled`.

    NFC 로 정규화한다 — macOS 는 자모를 분리해서(NFD) 보내는데, 그대로 두면
    눈에 같아 보이는 두 slug 이 다른 값이 된다.
    """
    text = unicodedata.normalize("NFC", title).strip().lower()
    text = _STRIP.sub("", text)
    text = _SPACES.sub("-", text)
    text = _DASHES.sub("-", text).strip("-")
    return text[:MAX_SLUG_LENGTH] or "untitled"


def unique_slug(base: str, taken: set[str]) -> str:
    """형제 중에 겹치면 `-2`, `-3` … 을 붙인다.

    실패로 되돌리지 않는다 — 같은 제목의 문서를 만드는 건 흔한 일이고,
    거기서 막으면 사용자가 제목을 억지로 비틀게 된다.
    """
    if base not in taken:
        return base
    # 접미사가 붙을 자리를 미리 비워 둔다. 자르고 나서 붙이면 상한을 넘는다.
    stem = base[: MAX_SLUG_LENGTH - 6]
    for n in range(2, 1000):
        candidate = f"{stem}-{n}"
        if candidate not in taken:
            return candidate
    raise ValueError("slug 후보를 찾지 못했다")


def join_path(parent_path: str, slug: str) -> str:
    return f"{parent_path}/{slug}" if parent_path else slug
