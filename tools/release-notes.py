#!/usr/bin/env python3
"""CHANGELOG 에서 한 판의 절을 뽑는다.

**한 벌만 둔다.** 릴리스 워크플로가 두 자리에서 이 규칙을 쓴다 — 이미지를
밀기 **전에** 절이 있는지 보는 자리(guard)와, 릴리스 본문을 쓰는 자리(notes).
두 벌을 두면 한쪽만 고쳐지는 날이 오고, 그날 릴리스는 반쪽으로 남는다.

왜 미리 보는가: 절이 없으면 `notes` 가 실패하는데 그때는 이미 ghcr 에
`ieum-api:<판>` 과 `ieum-web:<판>` 이 올라가 있다. 태그도 릴리스도 없는
이미지만 남고, 고쳐서 다시 돌리면 **같은 태그를 다른 바이트로 덮어쓴다** —
"같은 판이면 같은 바이트" 가 거기서 깨진다.

    python3 tools/release-notes.py 1.0.2            # 있는지만 본다
    python3 tools/release-notes.py 1.0.2 -o out.md  # 뽑아서 쓴다
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


def extract(changelog: str, version: str) -> str | None:
    """`## <판> ...` 절의 본문. 없으면 None."""
    match = re.search(rf"^## {re.escape(version)} .*?$(.*?)(?=^## |\Z)", changelog, re.S | re.M)
    return None if match is None else match.group(1).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="릴리스 판. 예) 1.0.2")
    parser.add_argument("-o", "--out", help="여기에 쓴다. 없으면 있는지만 본다")
    parser.add_argument("--changelog", default="CHANGELOG.md")
    args = parser.parse_args()

    text = pathlib.Path(args.changelog).read_text(encoding="utf-8")
    body = extract(text, args.version)
    if body is None:
        print(f"{args.changelog} 에 '## {args.version}' 절이 없다", file=sys.stderr)
        return 1
    if args.out:
        pathlib.Path(args.out).write_text(body + "\n", encoding="utf-8")
        print(f"{args.out} ({len(body)}자)")
    else:
        print(f"'## {args.version}' 절이 있다 ({len(body)}자)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
