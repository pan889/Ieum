"""IFM(ieum Flavored Markdown) 방언.

정본 명세는 `packages/markdown/dialect.json` 이고, 서버와 클라이언트가 각자
그대로 옮겨 적는다. 런타임에 그 파일을 읽지 않는 이유는 배포 산출물(휠·
이미지)에 저장소 레이아웃이 따라가지 않기 때문이다. 대신 **양쪽 테스트가
자기 설정과 명세 파일이 같은지 확인**한다 — 한쪽만 고치면 CI 가 잡는다
(wiki-markdown.md 1절).

`HTML_ENABLED = False` 가 여기서 제일 중요한 줄이다. 원시 HTML 을 켜는
순간 이슈 설명 한 줄로 XSS 가 된다.
"""

from __future__ import annotations

from functools import cache
from typing import Any
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

VERSION = 2
PRESET = "commonmark"
OPTIONS: dict[str, Any] = {
    # 원시 HTML 금지. 유일한 XSS 방어선이다.
    "html": False,
    "linkify": True,
    "typographer": False,
    "breaks": False,
}
#: 프리셋에 없어서 따로 켜는 규칙 (GFM).
CORE_RULES = ("table", "strikethrough")
PLUGINS = ("front_matter", "tasklists", "footnote")
#: 링크로 만들어 줄 스킴. attachment/page/issue/user 는 내부 URI 다 (4·5절).
#: 멘션은 `[@Alice](user:<uuid>)` 로 저장한다 — 이름이 바뀌어도 안 깨진다.
LINK_SCHEMES = ("http", "https", "mailto", "attachment", "page", "issue", "user")

_PLUGIN_FNS = {
    "front_matter": front_matter_plugin,
    "tasklists": tasklists_plugin,
    "footnote": footnote_plugin,
}


def as_spec() -> dict[str, Any]:
    """이 모듈의 설정을 명세 파일과 같은 모양으로. 테스트가 비교한다."""
    return {
        "version": VERSION,
        "preset": PRESET,
        "options": dict(OPTIONS),
        "core": list(CORE_RULES),
        "plugins": list(PLUGINS),
        "linkSchemes": list(LINK_SCHEMES),
    }


def validate_link(url: str) -> bool:
    """허용한 스킴만 링크로 만든다.

    markdown-it 기본 검사도 javascript: 를 막지만, 화이트리스트로 한 번 더
    좁힌다 — 상위 기본값이 바뀌어도 우리 정책은 그대로여야 한다.
    """
    stripped = url.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "/", "./", "../")):
        return True
    parsed = urlparse(stripped)
    if not parsed.scheme:
        return True
    return parsed.scheme.lower() in LINK_SCHEMES


@cache
def parser() -> MarkdownIt:
    """방언대로 설정한 파서. 상태가 없으므로 하나를 공유한다."""
    md = MarkdownIt(PRESET, OPTIONS)
    for rule in CORE_RULES:
        md.enable(rule)
    for name in PLUGINS:
        md.use(_PLUGIN_FNS[name])
    # markdown-it 이 훅을 인스턴스 속성으로 노출한다. 서브클래싱 없이
    # 갈아끼우는 게 라이브러리의 공식 방법이다.
    md.validateLink = validate_link  # type: ignore[method-assign]
    return md


__all__ = [
    "CORE_RULES",
    "LINK_SCHEMES",
    "OPTIONS",
    "PLUGINS",
    "PRESET",
    "VERSION",
    "as_spec",
    "parser",
    "validate_link",
]
