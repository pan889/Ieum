"""인쇄물 — PDF·Word 로 내보낸다 (B17).

## 화면과 같은 HTML 을 태운다

마크다운이 정본이고(ADR-0008) 이미 HTML 로 만드는 길이 있다. PDF 는 그
HTML 에 인쇄용 CSS 를 얹어 WeasyPrint 가 조판한다 — 인쇄용 렌더러를 따로
만들면 화면과 인쇄물이 **서로 다른 문서**가 되고, 그 차이는 인쇄한 뒤에야
보인다.

## 렌더러에게 네트워크를 주지 않는다

WeasyPrint 는 기본적으로 문서에 적힌 주소를 **가져온다.** 그러면 본문에
`<img src="http://169.254.169.254/...">` 를 쓴 사람이 서버의 자리에서 요청을
보낼 수 있다(SSRF). 그래서 `url_fetcher` 를 우리 것으로 바꿔 **첨부만**
내주고 나머지 스킴은 전부 거절한다 — `file:` 도, `https:` 도.

첨부 바이트는 부르는 쪽이 채운다. 거기가 ACL 을 보는 자리이기 때문이다.

## 화면에서만 채워지는 것은 그렇다고 적는다

`::chart`·`::issues` 같은 리프 디렉티브는 볼 때 서버에 물어 그린다. 인쇄물에
그 원문(`::chart{query=...}`)이 그대로 박히면 읽는 사람은 그것이 무엇인지
모르고, 지우면 자리에 무엇이 있었는지도 사라진다. 그래서 **자리와 이름을
남기고 화면에서 채워진다고 적는다.**

## 한글이 두부(□)로 나오는 실패

글꼴이 없으면 WeasyPrint 는 **터지지 않는다.** 사각형을 그린 멀쩡한 PDF 를
준다. 그래서 이미지에 CJK 글꼴을 깔아 두는 것이 이 기능의 일부다
(`apps/api/Dockerfile`).
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ieum.core.logging import get_logger
from ieum.core.markdown import to_html
from ieum.core.markdown.directives import LEAF_NAMES, parse_leaf

log = get_logger(__name__)

#: 인쇄물 한 부에 담을 장(章) 수의 상한.
#:
#: 스페이스 하나를 통째로 내보내면 문서 수만큼 조판한다. 상한이 없으면 큰
#: 스페이스 하나가 워커를 몇 분 잡는다 — 넘치면 **자르고 잘랐다고 적는다.**
MAX_CHAPTERS = 200

#: `attachment:` 스킴. 인쇄물이 이해하는 **유일한** 외부 주소다.
_ATTACHMENT_SCHEME = "attachment:"


@dataclass(frozen=True, slots=True)
class Asset:
    """인쇄물에 박아 넣을 파일 하나."""

    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class Chapter:
    """인쇄물의 한 장. 문서 하나가 한 장이다."""

    title: str
    #: 마크다운 정본. HTML 이 아니다 — 이 함수가 만든다.
    body: str


@dataclass(frozen=True, slots=True)
class Paper:
    """인쇄물 한 부."""

    title: str
    chapters: tuple[Chapter, ...]
    #: `attachment:<id>[/<파일명>]` → 파일. **부르는 쪽이 ACL 을 보고 채운다.**
    assets: Mapping[str, Asset] = field(default_factory=dict)
    #: 상한에 걸려 빠진 장 수. 0 이 아니면 인쇄물이 그 사실을 적는다.
    dropped: int = 0


# ── PDF ─────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def korean_is_renderable() -> bool:
    """한국어를 그릴 수 있는 글꼴이 이 기계에 있는가.

    **없으면 내보내기가 조용히 틀린 것을 만든다.** WeasyPrint 는 글꼴이 없어도
    터지지 않고, 한국어 글자를 텍스트 층에서 빼 버린 PDF 를 준다 — 파일은
    열리고 라틴 문자는 멀쩡하다. 내보낸 사람은 파일을 열고 나서야 알고, 로그에는
    아무것도 안 남는다. 그래서 여기서 한 번 보고 말한다.

    실제로 이것 때문에 CI 의 `test_paper.py` 여섯 개가 러너에서만 붉었고,
    단언이 `assert '가나다라' in '   '` 로 나와서 글꼴 문제로 보이지 않았다.

    fontconfig 에 물어본다(`fc-list :lang=ko`). 그것이 없는 기계라면 판단할
    근거가 없으므로 **참으로 둔다** — 있는지 모르는 것을 없다고 말하면 멀쩡한
    설치에 거짓 경보를 낸다.
    """
    try:
        found = subprocess.run(
            ["fc-list", ":lang=ko", "family"],  # noqa: S607 - PATH 로 찾는다
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if found.returncode != 0:
        return True
    return bool(found.stdout.strip())


def to_pdf(paper: Paper) -> bytes:
    """PDF 바이트. 장마다 새 쪽에서 시작한다."""
    from weasyprint import CSS, HTML  # 무거운 import 를 부를 때까지 미룬다

    if not korean_is_renderable():
        # 막지는 않는다 — 영어만 내보내는 사람에게는 이것이 문제가 아니다.
        # 다만 **로그에는 남는다.** 이것이 안 남으면 아무 데도 안 남는다.
        log.error(
            "paper.no_korean_font",
            hint="fonts-noto-cjk 를 설치하세요. 없으면 한국어가 빈칸으로 나갑니다.",
        )

    document = HTML(
        string=_document_html(paper),
        # **상대 주소가 디스크로 새지 않게** 베이스를 준다. 없으면 WeasyPrint 가
        # 현재 작업 디렉터리를 기준으로 `file:` 을 만든다.
        base_url="about:blank",
        url_fetcher=_fetcher(paper.assets),
    )
    rendered: bytes = document.write_pdf(stylesheets=[CSS(string=PRINT_CSS)])
    return rendered


def _fetcher(assets: Mapping[str, Asset]) -> Any:
    """WeasyPrint 가 주소를 만날 때 부르는 함수.

    **첨부만 내준다.** 다른 스킴은 값을 안 돌려주는 것이 아니라 예외를 낸다 —
    조용히 빈 것을 주면 그림이 안 뜬 이유가 안 보이고, 무엇보다 "가져올 수도
    있었는데 안 갔다" 와 "갈 수 없다" 가 구별되지 않는다.
    """

    def fetch(url: str) -> dict[str, Any]:
        if url.startswith(_ATTACHMENT_SCHEME):
            found = assets.get(url[len(_ATTACHMENT_SCHEME) :])
            if found is not None:
                return {"string": found.data, "mime_type": found.media_type}
            raise ValueError(f"인쇄물에 담기지 않은 첨부다: {url}")
        # http·https·file·data 전부 여기서 멈춘다.
        raise ValueError(f"인쇄물은 바깥 주소를 가져오지 않는다: {url}")

    return fetch


def _document_html(paper: Paper) -> str:
    chapters = paper.chapters[:MAX_CHAPTERS]
    dropped = paper.dropped + max(0, len(paper.chapters) - MAX_CHAPTERS)
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{_escape(paper.title)}</title></head><body>",
    ]
    for index, chapter in enumerate(chapters):
        # 첫 장은 쪽을 넘기지 않는다 — 문서 하나를 내보낼 때 빈 첫 쪽이 나온다.
        cls = "ieum-chapter" if index == 0 else "ieum-chapter ieum-break"
        parts.append(f"<section class='{cls}'>")
        parts.append(f"<h1 class='ieum-chapter-title'>{_escape(chapter.title)}</h1>")
        parts.append(for_print(chapter.body))
        parts.append("</section>")
    if dropped:
        parts.append(
            "<section class='ieum-chapter ieum-break'><p class='ieum-note'>"
            f"문서 {dropped}개가 상한을 넘어 빠졌습니다."
            "</p></section>"
        )
    parts.append("</body></html>")
    return "".join(parts)


# ── 인쇄용으로 HTML 을 손본다 ────────────────────────────────────

#: 리프 디렉티브 한 줄만 담긴 문단. `to_html` 은 이것을 글자로 내보낸다.
_DIRECTIVE_P = re.compile(r"<p>(::[^<]+)</p>")
#: 태스크 리스트의 체크박스. 인쇄에는 폼 컨트롤이 없다.
_CHECKED = re.compile(r"<input class=\"task-list-item-checkbox\" checked=\"checked\"[^>]*>")
_UNCHECKED = re.compile(r"<input class=\"task-list-item-checkbox\"[^>]*>")
#: 앱 안에서만 뜻이 있는 주소. 인쇄물에서 누를 수 없다.
_INTERNAL_HREF = re.compile(r"<a href=\"(?:page|issue|user):[^\"]*\">(.*?)</a>", re.DOTALL)


def for_print(body: str) -> str:
    """마크다운 본문을 **인쇄용** HTML 로.

    화면용과 다른 점만 손댄다:

    - 리프 디렉티브는 자리와 이름을 남긴다(화면에서 채워진다고 적는다).
    - 체크박스는 글자(☐·☑)로 바꾼다 — 인쇄에는 폼 컨트롤이 없다.
    - 앱 내부 링크는 글자만 남긴다. 종이에서 누를 수 없는 파란 글씨는
      읽는 사람을 헷갈리게 한다.
    """
    html = to_html(body)

    def note(match: re.Match[str]) -> str:
        line = _unescape(match.group(1))
        directive = parse_leaf(line)
        if directive is None or directive.name not in LEAF_NAMES:
            return match.group(0)
        return (
            f"<p class='ieum-live'>[{_escape(directive.name)}] 이 자리는 화면에서 채워집니다.</p>"
        )

    html = _DIRECTIVE_P.sub(note, html)
    html = _CHECKED.sub("<span class='ieum-check'>&#9745;</span>", html)
    html = _UNCHECKED.sub("<span class='ieum-check'>&#9744;</span>", html)
    html = _INTERNAL_HREF.sub(r"\1", html)
    return html


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _unescape(text: str) -> str:
    return (
        text.replace("&quot;", '"').replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    )


#: 인쇄용 스타일. 화면 CSS 를 재사용하지 않는다 — 화면은 스크롤과 호버가
#: 있는 매체이고 종이는 쪽이 있는 매체다.
#:
#: **쪽 번호를 넣는다.** 넣지 않으면 흩어진 인쇄물의 순서를 아무도 모른다.
PRINT_CSS = """
@page {
  size: A4;
  margin: 20mm 18mm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-size: 9pt;
    color: #666;
  }
}
body {
  font-family: sans-serif;
  font-size: 10.5pt;
  line-height: 1.6;
  color: #111;
}
.ieum-break { break-before: page; }
.ieum-chapter-title { font-size: 18pt; margin: 0 0 8mm; }
h1, h2, h3, h4, h5, h6 {
  line-height: 1.3;
  /* 제목만 남기고 쪽이 넘어가면 그 제목은 아무것도 안 이끈다. */
  break-after: avoid;
}
h2 { font-size: 14pt; margin: 8mm 0 3mm; }
h3 { font-size: 12pt; margin: 6mm 0 2mm; }
p, li { orphans: 2; widows: 2; }
code, pre {
  font-family: monospace;
  font-size: 9.5pt;
}
pre {
  background: #f6f7f9;
  border: 1pt solid #e3e5e8;
  border-radius: 2pt;
  padding: 3mm;
  /* 긴 줄이 쪽 밖으로 나가면 그 줄은 사라진다. */
  white-space: pre-wrap;
  word-break: break-word;
}
code { background: #f6f7f9; padding: 0 1pt; }
pre code { background: none; padding: 0; }
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 9.5pt;
}
th, td {
  border: 0.5pt solid #c8ccd0;
  padding: 1.5mm 2mm;
  text-align: left;
  vertical-align: top;
}
th { background: #f2f3f5; }
/* 표 머리글을 쪽마다 다시 그린다. 없으면 두 번째 쪽의 표는 열 뜻을 잃는다. */
thead { display: table-header-group; }
tr { break-inside: avoid; }
blockquote {
  margin: 3mm 0;
  padding: 0 0 0 4mm;
  border-left: 2pt solid #c8ccd0;
  color: #444;
}
img { max-width: 100%; }
.ieum-admonition {
  border: 0.5pt solid #c8ccd0;
  border-left: 3pt solid #8a8f95;
  padding: 2mm 3mm;
  margin: 3mm 0;
  break-inside: avoid;
}
.ieum-admonition-info { border-left-color: #2f6fd0; }
.ieum-admonition-warning { border-left-color: #c98a00; }
.ieum-admonition-danger { border-left-color: #c0392b; }
.ieum-live, .ieum-note {
  border: 0.5pt dashed #a6abb0;
  padding: 2mm 3mm;
  margin: 3mm 0;
  color: #555;
  font-size: 9.5pt;
}
.contains-task-list { list-style: none; padding-left: 0; }
.ieum-check { margin-right: 1mm; }
"""

# ── Word ────────────────────────────────────────────────────────
#
# `.docx` 는 HTML 을 안 받는다. 그래서 **토큰에서 만든다** — HTML 을 만들었다가
# 다시 파싱하면 우리가 두 번 해석하는 셈이고, 그 두 해석이 어긋나는 날이 온다
# (`to_plaintext` 도 같은 이유로 토큰을 본다).

#: 넣을 그림의 폭. Word 기본 여백(1인치씩)을 뺀 본문 폭이다.
_PICTURE_INCHES = 6.0

#: 이 확장자만 그림으로 넣는다. python-docx 는 모르는 형식에서 예외를 내고,
#: 그러면 문서 하나 때문에 내보내기가 통째로 실패한다.
_PICTURE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff"})


def to_docx(paper: Paper) -> bytes:
    """Word 바이트. 장마다 쪽을 넘긴다."""
    from docx import Document

    # **이 파일이 타입 경계다.** python-docx 는 `py.typed` 를 싣고도 일부
    # 함수에 주석이 없어서, 문서 객체를 그대로 들면 호출마다 mypy 가 막는다.
    # 여기서 한 번 좁히고 밖으로는 bytes 만 내보낸다.
    document: Any = Document()
    chapters = paper.chapters[:MAX_CHAPTERS]
    dropped = paper.dropped + max(0, len(paper.chapters) - MAX_CHAPTERS)
    for index, chapter in enumerate(chapters):
        if index:
            document.add_page_break()
        document.add_heading(chapter.title, level=1)
        _write_body(document, chapter.body, paper.assets)
    if dropped:
        document.add_page_break()
        document.add_paragraph(f"문서 {dropped}개가 상한을 넘어 빠졌습니다.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _write_body(document: Any, body: str, assets: Mapping[str, Asset]) -> None:
    from ieum.core.markdown import parser

    tokens = parser().parse(body)
    _write_blocks(document, tokens, 0, len(tokens), assets, depth=0, ordered=False)


# 토큰 종류만큼 갈래가 있다. 쪼개면 흐름이 더 안 읽힌다.
def _write_blocks(
    document: Any,
    tokens: list[Any],
    start: int,
    end: int,
    assets: Mapping[str, Asset],
    *,
    depth: int,
    ordered: bool,
) -> None:
    index = start
    while index < end:
        token = tokens[index]
        kind = token.type

        if kind == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[index + 1]
            # Word 의 제목 수준은 1..9 다. 장 제목이 1 이므로 한 칸 내린다 —
            # 안 내리면 문서 안의 `#` 이 장 제목과 같은 수준이 된다.
            paragraph = document.add_heading("", level=min(level + 1, 9))
            _write_runs(document, paragraph, inline.children or [], assets)
            index += 3
            continue

        if kind == "paragraph_open":
            inline = tokens[index + 1]
            directive = parse_leaf(inline.content.strip())
            if directive is not None and directive.name in LEAF_NAMES:
                document.add_paragraph(f"[{directive.name}] 이 자리는 화면에서 채워집니다.")
                index += 3
                continue
            style = _list_style(depth, ordered=ordered) if depth else None
            paragraph = document.add_paragraph(style=style)
            _write_runs(document, paragraph, inline.children or [], assets)
            index += 3
            continue

        if kind in {"bullet_list_open", "ordered_list_open"}:
            close = _matching(tokens, index, end)
            _write_blocks(
                document,
                tokens,
                index + 1,
                close,
                assets,
                depth=depth + 1,
                ordered=kind == "ordered_list_open",
            )
            index = close + 1
            continue

        if kind == "list_item_open":
            close = _matching(tokens, index, end)
            _write_blocks(document, tokens, index + 1, close, assets, depth=depth, ordered=ordered)
            index = close + 1
            continue

        if kind == "blockquote_open":
            close = _matching(tokens, index, end)
            # 인용 안의 문단은 인용 스타일로 나가야 한다. 깊이는 목록과
            # 별개이므로 0 으로 두고 스타일만 바꿔 쓴다.
            for inner in range(index + 1, close):
                if tokens[inner].type == "inline":
                    paragraph = document.add_paragraph(style="Intense Quote")
                    _write_runs(document, paragraph, tokens[inner].children or [], assets)
            index = close + 1
            continue

        if kind in {"fence", "code_block"}:
            _write_code(document, token.content)
            index += 1
            continue

        if kind == "table_open":
            close = _matching(tokens, index, end)
            _write_table(document, tokens, index, close, assets)
            index = close + 1
            continue

        if kind == "hr":
            # `.docx` 에 수평선 요소는 없다. 글자로 대신한다 — 문단 테두리를
            # 붙이려면 XML 을 직접 손대야 하고, 그건 워드 판마다 다르게 나온다.
            document.add_paragraph("─" * 30)
            index += 1
            continue

        index += 1


def _matching(tokens: list[Any], open_at: int, end: int) -> int:
    """`tokens[open_at]` 을 닫는 토큰의 자리.

    `nesting` 을 세어 찾는다. 태그 이름으로 찾으면 같은 태그가 겹쳐 있을 때
    (목록 안의 목록) 남의 닫는 태그를 자기 것으로 읽는다.
    """
    level = 0
    for index in range(open_at, end):
        level += tokens[index].nesting
        if level == 0:
            return index
    return end - 1


def _list_style(depth: int, *, ordered: bool) -> str:
    """목록 스타일 이름. python-docx 기본 템플릿의 것을 쓴다.

    깊이는 3단까지다(`List Bullet 3`). 더 깊으면 3단으로 붙인다 — 없는
    스타일 이름을 주면 예외가 나고, 그러면 목록을 깊게 쓴 문서 하나가
    내보내기를 통째로 막는다.
    """
    base = "List Number" if ordered else "List Bullet"
    return base if depth <= 1 else f"{base} {min(depth, 3)}"


def _write_code(document: Any, text: str) -> None:
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    run = paragraph.add_run(text.rstrip("\n"))
    run.font.name = "Courier New"
    run.font.size = Pt(9)


def _write_table(
    document: Any, tokens: list[Any], open_at: int, close_at: int, assets: Mapping[str, Asset]
) -> None:
    rows: list[list[Any]] = []
    for index in range(open_at, close_at):
        token = tokens[index]
        if token.type == "tr_open":
            rows.append([])
        elif token.type == "inline" and rows:
            rows[-1].append(token)
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=width)
    # 격자를 그린다. 테두리가 없으면 표가 그냥 붙어 있는 글자 덩어리다.
    table.style = "Table Grid"
    for row in rows:
        cells = table.add_row().cells
        for position, inline in enumerate(row[:width]):
            paragraph = cells[position].paragraphs[0]
            _write_runs(document, paragraph, inline.children or [], assets)


# 인라인 종류만큼 갈래가 있다.
def _write_runs(
    document: Any, paragraph: Any, children: list[Any], assets: Mapping[str, Asset]
) -> None:
    from docx.shared import Pt

    bold = False
    italic = False
    strike = False
    href: str | None = None
    for child in children:
        kind = child.type
        if kind == "strong_open":
            bold = True
        elif kind == "strong_close":
            bold = False
        elif kind == "em_open":
            italic = True
        elif kind == "em_close":
            italic = False
        elif kind == "s_open":
            strike = True
        elif kind == "s_close":
            strike = False
        elif kind == "html_inline":
            # **태스크 리스트의 체크박스가 여기로 온다.** 플러그인이 `<input>`
            # 을 날 HTML 조각으로 끼워 넣기 때문이다. 무시하면 끝낸 일과
            # 남은 일이 Word 에서 **똑같이 보인다** — 그런 체크리스트는
            # 인쇄해서 쓸 수 없다.
            mark = _task_mark(child.content)
            if mark is not None:
                paragraph.add_run(mark)
        elif kind == "link_open":
            href = str(child.attrs.get("href", ""))
        elif kind == "link_close":
            # **주소를 잃지 않는다.** `.docx` 에 진짜 하이퍼링크를 넣으려면
            # XML 을 직접 짜야 해서, 바깥 주소는 괄호로 적어 둔다 — 종이로
            # 나갔을 때도 어디를 가리켰는지 남는다. 앱 내부 주소(`page:` 등)는
            # 밖에서 뜻이 없으므로 글자만 남긴다.
            if href and href.startswith(("http://", "https://", "mailto:")):
                paragraph.add_run(f" ({href})").font.size = Pt(8)
            href = None
        elif kind == "code_inline":
            run = paragraph.add_run(child.content)
            run.font.name = "Courier New"
        elif kind == "image":
            _write_picture(document, paragraph, child, assets)
        elif kind == "softbreak":
            paragraph.add_run(" ")
        elif kind == "hardbreak":
            paragraph.add_run().add_break()
        elif kind == "text":
            run = paragraph.add_run(child.content)
            # **켤 때만 켠다.** `False` 를 넣으면 python-docx 가
            # `<w:b w:val="0"/>` 를 쓰고, 직접 서식은 스타일을 이긴다 — 제목
            # 문단에 그것이 붙으면 Word 에서 **제목이 굵지 않게** 나온다.
            # 값을 안 건드리면 스타일에서 물려받는다.
            if bold:
                run.bold = True
            if italic:
                run.italic = True
            if strike:
                run.font.strike = True


def _task_mark(html: str) -> str | None:
    """날 HTML 조각이 태스크 체크박스면 그 표시를, 아니면 `None`.

    본문에 사람이 쓴 HTML 도 이 자리로 온다. 그건 Word 에서 뜻이 없으므로
    지나가고, 우리가 아는 체크박스만 글자로 바꾼다.
    """
    if "task-list-item-checkbox" not in html:
        return None
    return "\u2611 " if 'checked="checked"' in html else "\u2610 "


def _write_picture(document: Any, paragraph: Any, token: Any, assets: Mapping[str, Asset]) -> None:
    """그림 하나. 넣을 수 없으면 **대체 글자를 남긴다.**

    파일이 없거나(지워진 첨부) 형식을 모르면 조용히 지나가면 안 된다 — 읽는
    사람은 그 자리에 무엇이 있었는지 알 수 없다.
    """
    from docx.shared import Inches

    src = str(token.attrs.get("src", ""))
    alt = token.content or src
    found = (
        assets.get(src[len(_ATTACHMENT_SCHEME) :]) if src.startswith(_ATTACHMENT_SCHEME) else None
    )
    if found is None or found.media_type not in _PICTURE_TYPES:
        paragraph.add_run(f"[그림: {alt}]")
        return
    try:
        document.add_picture(io.BytesIO(found.data), width=Inches(_PICTURE_INCHES))
    # 라이브러리가 무엇을 낼지 정하지 않았다. 그림 하나 때문에 내보내기가
    # 통째로 실패하는 것보다 대체 글자가 낫다.
    except Exception:
        paragraph.add_run(f"[그림: {alt}]")


__all__ = [
    "MAX_CHAPTERS",
    "PRINT_CSS",
    "Asset",
    "Chapter",
    "Paper",
    "for_print",
    "to_docx",
    "to_pdf",
]
