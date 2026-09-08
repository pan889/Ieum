"""쓰기 라우트는 커밋한다. 안 하면 API 가 201 을 주고 아무것도 저장하지 않는다.

이 결함은 **시험을 통과한다.** 서비스 단위 시험은 픽스처가 트랜잭션을 들고
있어서 flush 만으로도 다 보이고, 통합 시험도 같은 세션 안에서 읽으면 보인다.
실제로 desk 라우터를 그렇게 만들었고, HTTP 로 직접 몰아 보고서야
`POST /portals` 가 201 을 주고 테이블이 비어 있는 것을 봤다.

그래서 정적으로 본다. `conventions.md` 는 트랜잭션 경계를 **서비스**로
정해 두었는데 이 저장소의 실제 관행은 라우터 커밋이다(118개 중 113개).
그 관행을 규칙으로 굳히는 것이 아니라, **관행에서 벗어난 라우트를 눈에
보이게** 하는 것이 이 시험의 목적이다 — 아래 예외 목록에 이름을 올리려면
"왜 저장할 것이 없는가" 를 적어야 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ieum"
WRITE_METHODS = {"post", "patch", "put", "delete"}

#: 저장할 것이 없는 쓰기 메서드 라우트. 이름 옆에 이유를 적는다.
NO_WRITE = {
    # 전이 상태를 `state` 파라미터에 봉해 나간다 — DB 에 남기지 않는다.
    "start_sso",
    # 아래 다섯은 본문이 긴 **조회**다. POST 인 것은 IQL 을 URL 에 넣을 수
    # 없기 때문이고(길이·인코딩), 쓰는 것은 없다.
    "search_issues",
    "validate_iql",
    "suggest_iql",
    "export_issues",
    # 리포트도 같은 이유다 — 세기만 하고 아무것도 남기지 않는다 (A29).
    "count_issues",
    # 동시 편집의 표는 **Redis 에** 30초 살다 사라진다 (B16). Postgres 에
    # 남길 것이 없다: 표는 소켓 하나를 열 자격이고, 편집 결과는 방이
    # `page_collab` 에 스냅샷으로 저장한다. POST 인 것은 부를 때마다 새 표가
    # 나오기 때문이다 — 조회가 아니다.
    "mint_collab_ticket",
}


def _write_routes() -> list[tuple[str, str, str]]:
    """(파일, 함수, 소스) 목록. 데코레이터의 메서드 이름으로 고른다."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            methods = {
                d.func.attr
                for d in node.decorator_list
                if isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in WRITE_METHODS
            }
            if methods:
                found.append((path.name, node.name, ast.unparse(node)))
    return found


def _unreachable_tails() -> list[str]:
    """`return` **뒤에** 놓인 문장을 찾는다.

    "커밋 호출이 있다" 만 보면 이걸 놓친다 — 실제로 한 번 그랬다. 커밋을
    자동으로 끼워 넣다가 여러 줄 `return` 표현식 뒤에 붙였고, 소스에는
    `session.commit()` 이 멀쩡히 있는데 절대 실행되지 않았다. mypy 의
    `unreachable` 이 잡아 줬지만, 그건 우연히 그 규칙이 켜져 있어서다.
    여기서 명시적으로 본다.
    """
    dead: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for index, stmt in enumerate(node.body):
                if isinstance(stmt, ast.Return) and index < len(node.body) - 1:
                    dead.append(f"{path.name}:{node.name} (line {stmt.lineno})")
    return dead


def test_nothing_sits_after_a_return() -> None:
    dead = _unreachable_tails()
    assert not dead, "return 뒤에 실행되지 않는 문장이 있다:\n  " + "\n  ".join(dead)


def test_the_walk_finds_the_routes() -> None:
    """경로가 틀리면 아래 단언이 전부 참이 된다 — 그건 검사가 아니다."""
    assert len(_write_routes()) > 50


@pytest.mark.parametrize(
    ("module", "name"),
    [(m, n) for m, n, _ in _write_routes()],
    ids=lambda v: str(v),
)
def test_a_write_route_commits_or_is_listed(module: str, name: str) -> None:
    source = next(src for m, n, src in _write_routes() if (m, n) == (module, name))
    commits = "commit(" in source
    if name in NO_WRITE:
        assert not commits, f"{module}:{name} 이 예외 목록에 있는데 커밋한다. 목록에서 빼라."
        return
    assert commits, (
        f"{module}:{name} 이 커밋하지 않는다. 저장할 것이 없다면 이 파일의 "
        "NO_WRITE 에 이유와 함께 올려라 — 그냥 빠뜨린 것이면 API 가 성공을 "
        "돌려주고 아무것도 저장하지 않는다."
    )


def test_the_exception_list_has_no_ghosts() -> None:
    """사라진 라우트 이름이 목록에 남으면, 다음 사람이 같은 이름을 만들 때
    조용히 면제된다."""
    live = {n for _, n, _ in _write_routes()}
    ghosts = sorted(NO_WRITE - live)
    assert not ghosts, f"없는 라우트가 예외 목록에 남아 있다: {ghosts}"
