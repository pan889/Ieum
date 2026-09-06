"""디렉티브 = 매크로 (wiki-markdown.md 3절).

Confluence 매크로에 해당하는 기능을 마크다운 문법 안에서 표현한다. 마크다운
유효성을 깨지 않으므로 우리 뷰어가 아닌 곳에서도 **텍스트로는 읽힌다** —
그게 마크다운을 정본으로 고른 이유의 일부다.

```
::toc{depth=3}                       리프. 한 줄로 끝난다.
:::info                              컨테이너. 본문을 감싼다.
운영 중에는 이 절차를 쓰지 마세요.
:::
```

**모르는 이름은 그냥 텍스트다.** 거절하지 않는다 — 남의 도구가 만든 문서를
가져왔을 때 `::` 로 시작하는 줄 하나 때문에 문서 전체가 저장되지 않으면
임포트가 쓸모없어진다. 대신 **아는 이름은 인자까지 엄격하게** 본다. 지원한다고
말한 것에 대해서만 책임진다.

여기는 커널이라 IQL 을 모른다(모듈 경계). `::issues{query=…}` 의 질의가
문법적으로 옳은지는 보는 시점에 검색 API 가 판정하고, 여기서는 "비어 있지
않은가"까지만 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ieum.core.exceptions import ValidationError

#: 리프 디렉티브 한 줄. `::name` 또는 `::name{...}`.
LEAF_RE = re.compile(r"^::(?P<name>[a-zA-Z][a-zA-Z0-9_-]*)(?:\{(?P<attrs>[^}]*)\})?[ \t]*$")

#: 컨테이너 여는 줄. 닫는 줄은 `:::` 뿐이다.
CONTAINER_OPEN_RE = re.compile(
    r"^:::+(?P<name>[a-zA-Z][a-zA-Z0-9_-]*)(?:\{(?P<attrs>[^}]*)\})?[ \t]*$"
)

_ATTR_RE = re.compile(
    r'(?P<key>[a-zA-Z][a-zA-Z0-9_-]*)(?:=(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"}]+)))?'
)

#: 문서 목차. 이 문서의 제목들로 만든다.
TOC = "toc"
#: 하위 문서 목록. 위키에서만 뜻이 있다.
CHILDREN = "children"
#: IQL 결과 표. 권한은 서버가 거른다.
ISSUES = "issues"
#: 다른 문서의 앞부분. 권한은 서버가 거른다 — 못 보는 문서는 존재도 모른다.
EXCERPT = "excerpt"

LEAF_NAMES = (TOC, CHILDREN, ISSUES, EXCERPT)

#: 강조 상자. 이름이 곧 톤이다.
CONTAINER_NAMES = ("info", "note", "tip", "warning", "danger")

#: `::issues` 가 그릴 수 있는 열. 여기 없는 이름은 거절한다 — 오타를
#: 조용히 빈 칸으로 그리면 왜 안 나오는지 알 수 없다. 목록 응답이 실제로
#: 담아 오는 것만 넣는다(유형은 요약에 없다).
ISSUE_COLUMNS = ("key", "summary", "status", "assignee", "priority", "due_date", "updated")

MAX_TOC_DEPTH = 6
MAX_CHILDREN_DEPTH = 6
MAX_ISSUE_ROWS = 100
DEFAULT_ISSUE_ROWS = 20


@dataclass(frozen=True, slots=True)
class Directive:
    name: str
    attrs: dict[str, str]
    #: 컨테이너면 감싼 본문. 리프면 None.
    body: str | None = None

    @property
    def is_container(self) -> bool:
        return self.body is not None


def parse_attrs(raw: str) -> dict[str, str]:
    """`{a=1 b="두 낱말"}` 안쪽을 읽는다. 값 없는 키는 빈 문자열."""
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw):
        quoted, bare = match.group("quoted"), match.group("bare")
        attrs[match.group("key").lower()] = quoted if quoted is not None else (bare or "")
    return attrs


def parse_leaf(line: str) -> Directive | None:
    """리프 디렉티브 한 줄. 아니면 None."""
    match = LEAF_RE.match(line)
    if not match:
        return None
    return Directive(
        name=match.group("name").lower(), attrs=parse_attrs(match.group("attrs") or "")
    )


def is_known(name: str, *, container: bool) -> bool:
    return name in (CONTAINER_NAMES if container else LEAF_NAMES)


def validate(directive: Directive) -> None:
    """아는 디렉티브의 인자를 본다. 모르는 이름은 그냥 지나간다.

    저장 시점에 부른다. 보는 시점에 처음 알면, 쓴 사람은 이미 떠나고 없다.
    """
    name = directive.name
    if directive.is_container:
        if name in CONTAINER_NAMES:
            _reject_unknown_keys(directive, {"title"})
        return
    if name == TOC:
        _reject_unknown_keys(directive, {"depth"})
        _depth(directive, MAX_TOC_DEPTH)
    elif name == CHILDREN:
        _reject_unknown_keys(directive, {"depth", "page"})
        _depth(directive, MAX_CHILDREN_DEPTH)
    elif name == ISSUES:
        _reject_unknown_keys(directive, {"query", "columns", "limit"})
        _issues(directive)
    elif name == EXCERPT:
        _reject_unknown_keys(directive, {"page"})
        _excerpt(directive)


def _fail(directive: Directive, message: str, *, code: str, **details: Any) -> ValidationError:
    """무엇이 틀렸는지 코드로 구분한다.

    코드 하나에 "인자가 올바르지 않다" 로 뭉치면, 번역된 화면에서는 어느
    인자인지 알 수 없다 — 클라이언트는 서버 문구가 아니라 코드로 번역한다
    (i18n.md 1절). 그래서 경우마다 코드를 나누고 필요한 값을 details 로 준다.
    """
    return ValidationError(message, code=code, details={"directive": directive.name, **details})


def _reject_unknown_keys(directive: Directive, allowed: set[str]) -> None:
    unknown = sorted(set(directive.attrs) - allowed)
    if unknown:
        raise _fail(
            directive,
            f"`::{directive.name}` 이 모르는 인자다: {', '.join(unknown)}",
            code="markdown.unknown_directive_arg",
            unknown=", ".join(unknown),
            allowed=", ".join(sorted(allowed)),
        )


def _depth(directive: Directive, maximum: int) -> None:
    raw = directive.attrs.get("depth")
    if raw is None or raw == "":
        return
    if not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise _fail(
            directive,
            f"depth 는 1 이상 {maximum} 이하의 정수여야 한다.",
            code="markdown.invalid_directive_depth",
            value=raw,
            max=maximum,
        )


#: `SPACE/조각/조각`. 스페이스 키는 대문자·숫자, 나머지는 슬러그다.
_PAGE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(?:/[^/\s][^/]*)*$")


def _excerpt(directive: Directive) -> None:
    path = directive.attrs.get("page", "").strip()
    if not path:
        # 어느 문서인지 없으면 그릴 것이 정해지지 않는다. "이 문서" 를 기본으로
        # 두면 자기를 끌어와 무한히 감긴다.
        raise _fail(
            directive,
            "`::excerpt` 에는 page 가 필요하다.",
            code="markdown.directive_needs_page",
        )
    if not _PAGE_PATH_RE.match(path):
        raise _fail(
            directive,
            f"page 는 `SPACE/경로` 모양이어야 한다: {path}",
            code="markdown.invalid_page_path",
            value=path,
        )


def _issues(directive: Directive) -> None:
    query = directive.attrs.get("query", "").strip()
    if not query:
        # 질의가 없으면 무엇을 그려야 할지 정해지지 않는다. 전체를 그리는
        # 기본값을 두면 실수로 5만 건짜리 표가 문서에 박힌다.
        raise _fail(
            directive,
            "`::issues` 에는 query 가 필요하다.",
            code="markdown.directive_needs_query",
        )

    raw_columns = directive.attrs.get("columns", "").strip()
    if raw_columns:
        unknown = [c for c in _split_columns(raw_columns) if c not in ISSUE_COLUMNS]
        if unknown:
            raise _fail(
                directive,
                f"그릴 수 없는 열이다: {', '.join(unknown)}",
                code="markdown.unknown_issue_column",
                unknown=", ".join(unknown),
                allowed=", ".join(ISSUE_COLUMNS),
            )

    limit = directive.attrs.get("limit", "").strip()
    if limit and (not limit.isdigit() or not 1 <= int(limit) <= MAX_ISSUE_ROWS):
        raise _fail(
            directive,
            f"limit 은 1 이상 {MAX_ISSUE_ROWS} 이하의 정수여야 한다.",
            code="markdown.invalid_directive_limit",
            value=limit,
            max=MAX_ISSUE_ROWS,
        )


def _split_columns(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def validate_source(text: str) -> None:
    """본문 전체를 훑어 아는 디렉티브를 검사한다.

    코드 블록 안의 `::toc` 는 예제지 디렉티브가 아니다. 펜스 안은 건너뛴다.
    """
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue

        container = CONTAINER_OPEN_RE.match(line)
        if container:
            directive = Directive(
                name=container.group("name").lower(),
                attrs=parse_attrs(container.group("attrs") or ""),
                body="",
            )
        else:
            parsed = parse_leaf(line)
            if parsed is None:
                continue
            directive = parsed
        validate(directive)


__all__ = [
    "CHILDREN",
    "CONTAINER_NAMES",
    "CONTAINER_OPEN_RE",
    "DEFAULT_ISSUE_ROWS",
    "ISSUES",
    "ISSUE_COLUMNS",
    "LEAF_NAMES",
    "LEAF_RE",
    "MAX_CHILDREN_DEPTH",
    "MAX_ISSUE_ROWS",
    "MAX_TOC_DEPTH",
    "TOC",
    "Directive",
    "is_known",
    "parse_attrs",
    "parse_leaf",
    "validate",
    "validate_source",
]
