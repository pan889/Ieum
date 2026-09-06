"""서버측 번역.

알림·이메일은 **수신자 언어**로 렌더한다 (docs/architecture/i18n.md 3절).
발신자 기준으로 렌더하면 한국어 사용자가 만든 이슈의 알림이 영어권
담당자에게 한국어로 간다.

프론트와 **같은 JSON 카탈로그**를 읽는다. 서버가 별도 문구를 갖기 시작하면
두 벌이 어긋난다. ICU 는 서브셋만 지원한다 — 서버가 렌더하는 문자열은
알림 제목·본문 정도라 plural/select 와 단순 치환이면 충분하다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ieum.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_LOCALE = "en"
#: {name} 또는 {count, plural, one {...} other {...}}
_PLACEHOLDER = re.compile(r"\{(\w+)(?:,\s*(plural|select)\s*,\s*(.*?))?\}(?=[^}]*$|)")
_SIMPLE = re.compile(r"\{(\w+)\}")


@lru_cache(maxsize=32)
def _catalog(catalog_dir: str, locale: str) -> dict[str, str]:
    """`<dir>/<locale>/*.json` 을 하나로 합친다. 키는 `namespace:key`."""
    base = Path(catalog_dir) / locale
    merged: dict[str, str] = {}
    if not base.is_dir():
        log.warning("i18n.catalog_missing", locale=locale, path=str(base))
        return merged
    for file in sorted(base.glob("*.json")):
        namespace = file.stem
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("i18n.catalog_unreadable", file=str(file), error=str(exc))
            continue
        for key, value in data.items():
            if isinstance(value, str):
                merged[f"{namespace}:{key}"] = value
    return merged


class Translator:
    """한 로케일에 묶인 번역기. 수신자마다 하나씩 만든다."""

    def __init__(self, catalog_dir: Path, locale: str) -> None:
        self._dir = str(catalog_dir)
        self.locale = locale
        self._messages = _catalog(self._dir, locale)
        self._fallback = _catalog(self._dir, DEFAULT_LOCALE) if locale != DEFAULT_LOCALE else {}

    def __call__(self, key: str, /, **params: Any) -> str:
        return self.translate(key, **params)

    def translate(self, key: str, /, **params: Any) -> str:
        """번역문이 없으면 en 으로, 그것도 없으면 키를 그대로 돌려준다.

        키를 그대로 내보내는 게 빈 문자열보다 낫다 — 사용자가 무엇이
        비었는지 알 수 있고, 우리는 로그에서 잡을 수 있다.
        """
        template = self._messages.get(key) or self._fallback.get(key)
        if template is None:
            log.warning("i18n.missing_key", key=key, locale=self.locale)
            return key
        return render(template, params)


def render(template: str, params: dict[str, Any]) -> str:
    """ICU 서브셋 렌더. plural/select 와 단순 치환만 지원한다."""
    result = _render_choices(template, params)
    return _SIMPLE.sub(lambda m: str(params.get(m.group(1), m.group(0))), result)


def _render_choices(template: str, params: dict[str, Any]) -> str:
    """`{count, plural, one {# 개} other {# 개들}}` 를 펼친다."""
    out: list[str] = []
    index = 0
    while True:
        start = template.find("{", index)
        if start == -1:
            out.append(template[index:])
            break
        end = _matching_brace(template, start)
        if end == -1:
            out.append(template[index:])
            break

        inner = template[start + 1 : end]
        parts = inner.split(",", 2)
        if len(parts) == 3 and parts[1].strip() in {"plural", "select"}:
            name, kind, body = parts[0].strip(), parts[1].strip(), parts[2]
            out.append(template[index:start])
            out.append(_choose(kind, name, body, params))
        else:
            out.append(template[index : end + 1])
        index = end + 1
    return "".join(out)


def _choose(kind: str, name: str, body: str, params: dict[str, Any]) -> str:
    options = _options(body)
    value = params.get(name)

    if kind == "plural":
        count = int(value) if isinstance(value, int | float) else 0
        # 한국어처럼 복수형이 없는 언어는 other 만 채워 둔다.
        selected = (
            options.get(f"={count}")
            or (options.get("one") if count == 1 else None)
            or options.get("other")
            or ""
        )
        return render(selected.replace("#", str(count)), params)

    selected = options.get(str(value)) or options.get("other") or ""
    return render(selected, params)


def _options(body: str) -> dict[str, str]:
    """`one {..} other {..}` 를 딕셔너리로."""
    result: dict[str, str] = {}
    index = 0
    while index < len(body):
        brace = body.find("{", index)
        if brace == -1:
            break
        keyword = body[index:brace].strip()
        close = _matching_brace(body, brace)
        if close == -1:
            break
        if keyword:
            result[keyword] = body[brace + 1 : close]
        index = close + 1
    return result


def _matching_brace(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def translator_for(catalog_dir: Path, locale: str | None) -> Translator:
    return Translator(catalog_dir, locale or DEFAULT_LOCALE)
