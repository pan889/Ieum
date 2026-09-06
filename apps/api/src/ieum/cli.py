"""운영 CLI. `python -m ieum.cli <command>`"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _cmd_openapi() -> int:
    """OpenAPI 스키마를 stdout 으로 덤프한다 (make api-client 가 사용)."""
    from ieum.main import create_app

    print(json.dumps(create_app().openapi(), indent=2, ensure_ascii=False))
    return 0


def _cmd_seed() -> int:
    """기본 워크스페이스·내장 역할·관리자 계정을 만든다. 멱등하다."""
    from ieum.seed import run_seed

    return asyncio.run(run_seed())


def _cmd_seed_fields() -> int:
    """데모용 커스텀 필드 정의를 넣는다 (운영 시드와 분리). 멱등하다."""
    from ieum.demo_fields import run_seed_fields

    return asyncio.run(run_seed_fields())


def _cmd_reindex() -> int:
    """검색 색인을 다시 만든다. 멱등하다."""
    from ieum.reindex import run_reindex

    return asyncio.run(run_reindex())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ieum")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("openapi", help="OpenAPI 스키마 덤프")
    sub.add_parser("seed", help="초기 데이터 생성 (멱등)")
    sub.add_parser("seed-fields", help="데모용 커스텀 필드 정의 생성 (멱등)")
    sub.add_parser("reindex", help="검색 색인 재생성 (멱등)")

    args = parser.parse_args(argv)
    match args.command:
        case "openapi":
            return _cmd_openapi()
        case "seed":
            return _cmd_seed()
        case "seed-fields":
            return _cmd_seed_fields()
        case "reindex":
            return _cmd_reindex()
        case _:  # pragma: no cover
            parser.error(f"알 수 없는 명령: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
