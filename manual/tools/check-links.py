"""빌드된 사이트의 **내부 링크를 전부 따라가 본다.**

`mkdocs build --strict` 는 마크다운 소스의 링크를 본다. 이건 결과물의
`href` 를 본다 — 둘이 다른 것을 잡는다:

- nav 가 만든 경로, 테마가 만든 경로(이전/다음, 편집 링크)
- 절대 링크(`site_url` 의 경로 밑을 가리키는 것). 베이스 경로를 바꾸면
  여기가 먼저 깨진다.

    python3 tools/check-links.py site

앵커(`#...`)는 안 본다. 문서를 옮길 때 깨지는 것은 대개 경로이고, 앵커까지
보려면 제목의 slug 규칙을 이쪽에 한 벌 더 가져야 한다.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path

HREF = re.compile(r'href="([^"]+)"')
EXTERNAL = ("http://", "https://", "mailto:", "#", "data:", "javascript:")


def base_path(site: Path) -> str:
    """`site_url` 의 경로. 절대 링크가 이 밑을 가리킨다.

    `mkdocs.yml` 을 파싱하지 않고 **결과물에서 읽는다** — 빌드된 것이 무엇을
    가리키는지가 알고 싶은 것이고, 설정과 결과가 갈릴 수도 있다.

    `index.html` 만 보면 안 된다: 보통 페이지는 자산을 **상대 경로**로
    가리키고, 절대 경로를 쓰는 것은 `404.html` 뿐이다(어느 깊이에서 열릴지
    모르므로). 그래서 전부 훑어 절대 자산 경로를 하나 찾는다.
    """
    for page in sorted(site.rglob("*.html")):
        for href in HREF.findall(page.read_text(errors="replace")):
            if href.startswith("/") and "/assets/" in href:
                return href.split("assets/")[0]
    return "/"


def main(argv: list[str]) -> int:
    site = Path(argv[1] if len(argv) > 1 else "site")
    if not site.is_dir():
        print(f"빌드된 사이트가 없다: {site}", file=sys.stderr)
        return 2

    base = base_path(site)
    pages = sorted(site.rglob("*.html"))
    broken: list[str] = []

    for page in pages:
        for href in HREF.findall(page.read_text(errors="replace")):
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

    if broken:
        print(f"깨진 내부 링크 {len(broken)}건:", file=sys.stderr)
        for line in broken:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"내부 링크 확인 통과 — 페이지 {len(pages)}개, 베이스 {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
