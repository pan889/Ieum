"""빌드된 사이트의 **내부 링크를 전부 따라가 본다.**

`mkdocs build --strict` 는 마크다운 소스의 링크를 본다. 이건 결과물의
`href` 와 `src` 를 본다 — 둘이 다른 것을 잡는다:

- nav 가 만든 경로, 테마가 만든 경로(이전/다음, 편집 링크)
- 절대 링크(`site_url` 의 경로 밑을 가리키는 것). 베이스 경로를 바꾸면
  여기가 먼저 깨진다.
- **날 HTML 로 적은 그림.** 마크다운으로 적은 `![...](없는것.png)` 는
  `--strict` 가 잡는다 — 넣어 보고 확인했다. 하지만
  `<img src="없는것.png">` 는 **통과한다.** 마크다운 링크가 아니라서
  mkdocs 의 눈에 안 들어오고, 화면에는 깨진 그림이 뜬다. 테마 오버라이드와
  `grid cards` 처럼 HTML 을 직접 적는 자리가 있으므로 여기서 본다.

**두 언어를 다 본다.** 영어판은 `site/en/` 밑에 같은 모양으로 나가므로 위의
것을 그대로 따라간다. 거기에 하나를 더 본다:

- **본문 링크가 다른 언어판으로 새지 않는가.** 영어판 본문의 `install.md`
  링크는 i18n 플러그인이 `en/install/` 로 잇는다. 번역하면서 링크를 손으로
  `../../install/` 처럼 적거나 한국어판 주소를 박으면, 링크는 살아 있어
  위의 확인을 통과하지만 영어로 읽던 사람이 한국어 쪽에 떨어진다. 그림
  (`assets/`)은 두 판이 같이 쓰므로 괜찮다. 언어 메뉴는 본문 밖이라 안 본다.

    python3 tools/check-links.py site

앵커(`#...`)는 안 본다. 문서를 옮길 때 깨지는 것은 대개 경로이고, 앵커까지
보려면 제목의 slug 규칙을 이쪽에 한 벌 더 가져야 한다.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path

#: 앞의 공백이 있어야 한다 — 없으면 `data-src="..."` 같은 테마 속성까지 집는다.
REF = re.compile(r'\s(?:href|src)="([^"]+)"')
EXTERNAL = ("http://", "https://", "mailto:", "#", "data:", "javascript:")
HTML_LANG = re.compile(r'<html[^>]*\slang="([^"]+)"')
#: 본문. Material 은 쪽마다 `<article class="md-content__inner ...">` 하나를 쓴다.
ARTICLE = re.compile(r"<article\b.*?</article>", re.S)


def language_roots(site: Path) -> dict[str, str]:
    """언어판의 뿌리 폴더 → 언어. 기본 언어는 `""` (사이트 뿌리)다.

    설정을 읽지 않고 **결과물에서** 찾는다 — 위의 `base_path` 와 같은 이유다.
    폴더 이름이 그 첫 화면의 `<html lang>` 과 같으면 언어판으로 친다(`en/`).
    """
    roots: dict[str, str] = {}
    home = site / "index.html"
    if home.exists() and (m := HTML_LANG.search(home.read_text(errors="replace"))):
        roots[""] = m.group(1)
    for child in sorted(p for p in site.iterdir() if p.is_dir()):
        index = child / "index.html"
        if not index.exists():
            continue
        m = HTML_LANG.search(index.read_text(errors="replace"))
        if m and m.group(1) == child.name:
            roots[child.name] = child.name
    return roots


def language_of(path: Path, site: Path, roots: dict[str, str]) -> str | None:
    """결과물 안의 경로가 어느 언어판에 속하는가."""
    parts = path.relative_to(site).parts
    if parts and parts[0] in roots:
        return roots[parts[0]]
    return roots.get("")


def base_path(site: Path) -> str:
    """`site_url` 의 경로. 절대 링크가 이 밑을 가리킨다.

    `mkdocs.yml` 을 파싱하지 않고 **결과물에서 읽는다** — 빌드된 것이 무엇을
    가리키는지가 알고 싶은 것이고, 설정과 결과가 갈릴 수도 있다.

    `index.html` 만 보면 안 된다: 보통 페이지는 자산을 **상대 경로**로
    가리키고, 절대 경로를 쓰는 것은 `404.html` 뿐이다(어느 깊이에서 열릴지
    모르므로). 그래서 전부 훑어 절대 자산 경로를 하나 찾는다.
    """
    for page in sorted(site.rglob("*.html")):
        for href in REF.findall(page.read_text(errors="replace")):
            if href.startswith("/") and "/assets/" in href:
                return href.split("assets/")[0]
    return "/"


def main(argv: list[str]) -> int:
    site = Path(argv[1] if len(argv) > 1 else "site")
    if not site.is_dir():
        print(f"빌드된 사이트가 없다: {site}", file=sys.stderr)
        return 2

    base = base_path(site)
    roots = language_roots(site)
    site = site.resolve()
    pages = sorted(site.rglob("*.html"))
    broken: list[str] = []
    leaks: list[str] = []

    for page in pages:
        html = page.read_text(errors="replace")
        for href in REF.findall(html):
            if href.startswith(EXTERNAL):
                continue
            target = urllib.parse.unquote(href.partition("#")[0])
            if not target:
                continue
            if target.startswith("/"):
                if not target.startswith(base):
                    broken.append(f"{page.relative_to(site)} → {href} (베이스 {base} 밖)")
                    continue
                resolved = site / target[len(base) :]
            else:
                resolved = page.parent / target
            resolved = Path(str(resolved).rstrip("/")).resolve()
            if resolved.is_dir():
                resolved = resolved / "index.html"
            if not resolved.exists():
                broken.append(f"{page.relative_to(site)} → {href}")

        # 본문 링크가 다른 언어판의 **쪽**으로 가는가. 404 는 어느 판에서
        # 열릴지 모르는 쪽이라 뺀다.
        if page.name == "404.html" and page.parent == site:
            continue
        here = language_of(page, site, roots)
        for article in ARTICLE.findall(html):
            for href in REF.findall(article):
                if href.startswith(EXTERNAL):
                    continue
                target = urllib.parse.unquote(href.partition("#")[0])
                if not target or target.startswith("/"):
                    continue
                resolved = Path(str(page.parent / target).rstrip("/")).resolve()
                if resolved.is_dir():
                    resolved = resolved / "index.html"
                if resolved.suffix != ".html" or not resolved.is_relative_to(site):
                    continue
                there = language_of(resolved, site, roots)
                if there != here:
                    leaks.append(f"{page.relative_to(site)} ({here}) → {href} ({there})")

    if broken:
        print(f"깨진 내부 참조 {len(broken)}건:", file=sys.stderr)
        for line in broken:
            print(f"  - {line}", file=sys.stderr)
    if leaks:
        print(f"본문이 다른 언어판으로 새는 링크 {len(leaks)}건:", file=sys.stderr)
        for line in leaks:
            print(f"  - {line}", file=sys.stderr)
    if broken or leaks:
        return 1
    counts = ", ".join(
        f"{lang} {sum(1 for p in pages if language_of(p, site, roots) == lang)}"
        for lang in dict.fromkeys(roots.values())
    )
    print(f"내부 링크·그림 확인 통과 — 페이지 {len(pages)}개({counts}), 베이스 {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
