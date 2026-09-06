"""정규화기에게 `:::` 컨테이너를 가르친다.

mdformat 은 `:::info` 를 그냥 문단으로 본다. 그러면 안쪽에 목록이 있을 때
닫는 `:::` 이 마지막 항목의 이어진 줄로 빨려 들어간다:

```
:::warning          →   :::warning
- 항목
- 항목                  - 항목
:::                     - 항목
                          :::        ← 목록 안으로 들어갔다
```

멱등하기까지 해서 테스트로도 안 잡힌다 — 두 번 돌려도 같은 (망가진) 결과가
나온다. 컨테이너를 하나의 블록으로 인식시켜야 고쳐진다.

mdformat 확장은 보통 엔트리포인트로 등록하지만, 엔트리포인트는 **설치
시점**에 dist-info 에 박힌다. 편집 설치된 개발 환경과 이미 만들어진 컨테이너
이미지에서는 소스를 고쳐도 반영되지 않는다 — 로컬에서 되던 게 배포에서
안 되는 종류다. 그래서 import 시점에 레지스트리에 직접 등록한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import mdformat.plugins
from markdown_it import MarkdownIt
from mdformat.renderer import RenderContext, RenderTreeNode
from mdit_py_plugins.colon_fence import colon_fence_plugin

#: 안쪽 본문을 다시 정규화하며 들어갈 수 있는 깊이. 넘으면 원문 그대로 둔다.
#: 상한이 없으면 깊게 중첩된 문서 하나가 정규화기를 오래 붙든다.
MAX_NESTING = 4

_depth = 0

EXTENSION_ID = "ieum_colon_fence"


def update_mdit(mdit: MarkdownIt) -> None:
    mdit.use(colon_fence_plugin)


def _render_colon_fence(node: RenderTreeNode, context: RenderContext) -> str:
    info = node.info.strip()
    # 안쪽도 마크다운이다. 정규화하지 않으면 상자 안에서만 서식이 어긋난다.
    body = _format_inner(node.content, context)
    # 본문에 `:::` 이 있으면 더 긴 울타리로 감싼다 — 안 그러면 거기서 끊긴다.
    fence = ":" * max(3, _longest_colon_run(body) + 1)
    return f"{fence}{info}\n{body}{fence}" if body else f"{fence}{info}\n{fence}"


def _format_inner(content: str, context: RenderContext) -> str:
    global _depth
    if not content.strip():
        return ""
    if _depth >= MAX_NESTING:
        return content if content.endswith("\n") else content + "\n"

    import mdformat

    _depth += 1
    try:
        formatted = mdformat.text(content, extensions=_extensions(context))
    except Exception:
        # 안쪽이 이상해도 바깥 문서까지 못 쓰게 만들지 않는다.
        return content if content.endswith("\n") else content + "\n"
    finally:
        _depth -= 1
    return formatted


def _extensions(context: RenderContext) -> set[str]:
    parser_extensions: Any = context.options.get("parser_extension", [])
    names = {
        name
        for name, module in mdformat.plugins.PARSER_EXTENSIONS.items()
        if module in parser_extensions
    }
    # 우리 자신은 항상 켜 둔다. 빠지면 중첩 컨테이너가 다시 문단이 된다.
    names.add(EXTENSION_ID)
    return names


def _longest_colon_run(text: str) -> int:
    longest = current = 0
    for char in text:
        current = current + 1 if char == ":" else 0
        longest = max(longest, current)
    return longest


RENDERERS: Mapping[str, Any] = {"colon_fence": _render_colon_fence}
CHANGES_AST = False

# mdformat 은 이름으로 확장을 찾는다. 엔트리포인트 대신 여기서 넣는다(위 참고).
cast("dict[str, Any]", mdformat.plugins.PARSER_EXTENSIONS)[EXTENSION_ID] = __import__(
    __name__, fromlist=["_"]
)

__all__ = ["CHANGES_AST", "EXTENSION_ID", "MAX_NESTING", "RENDERERS", "update_mdit"]
