"""운영 CLI. `python -m ieum.cli <command>`"""

from __future__ import annotations

import argparse
import json
import sys


def _cmd_openapi() -> int:
    """OpenAPI 스키마를 stdout 으로 덤프한다 (make api-client 가 사용)."""
    from ieum.main import create_app

    print(json.dumps(create_app().openapi(), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ieum")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("openapi", help="OpenAPI 스키마 덤프")

    args = parser.parse_args(argv)
    match args.command:
        case "openapi":
            return _cmd_openapi()
        case _:  # pragma: no cover
            parser.error(f"알 수 없는 명령: {args.command}")
            return 2


if __name__ == "__main__":
    sys.exit(main())
