"""Redmine → 이관 묶음.

**관리자의 기계에서 돈다**(ADR-0016). Ieum 서버는 소스 쪽으로 나가지 않는다.

    python -m ieum.migrate.redmine \\
        --url https://redmine.example.com \\
        --project my-project \\
        --out my-project.zip

API 키는 `--key` 로도 받지만 **`REDMINE_API_KEY` 환경변수를 권한다** — 명령줄
인자는 그 기계의 프로세스 목록과 셸 히스토리에 남는다. 어느 쪽이든 이 프로그램은
키를 **출력하지 않는다.**

## 왜 이슈마다 한 번씩 더 두드리는가

코멘트(journals)는 목록 API 가 안 준다. 이슈 하나씩 받아야 나온다 — Redmine 에
묶어 주는 길이 없다. 이관은 한 번 하는 일이라 받아들이고, 대신 진행 상황을
stderr 로 흘린다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from ieum.migrate.archive import (
    Archive,
    Comment,
    Issue,
    Manifest,
    Person,
    Project,
    Relation,
    Source,
    write_archive,
)

ADAPTER = "ieum-migrate-redmine/1"

#: 한 번에 받을 개수. Redmine 이 100 을 넘겨 주지 않는다.
PAGE = 100
TIMEOUT = 60


class RedmineError(RuntimeError):
    """소스를 읽다가 멈췄다. 메시지에 **키를 담지 않는다.**"""


def _http_url(base_url: str) -> str:
    """`--url` 을 http/https 로 못 박는다.

    `urlopen` 은 스킴을 가리지 않는다 — `file:///etc/passwd` 를 주면 파일을
    연다. 이 도구는 관리자의 기계에서 돌지만 **주소는 사람이 치는 값이
    아닐 수도 있다**(스크립트·설정 파일·CI 변수). 그리고 이 코드는 언젠가
    서버 쪽으로 옮겨 붙는 종류다 — 그때 이 검사가 없으면 SSRF 통로가 된다.
    ADR-0016 이 통째로 그 걱정 위에 서 있다.
    """
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RedmineError(f"http 또는 https 주소여야 한다: {base_url!r}")
    return base_url.rstrip("/")


class Client:
    """Redmine REST 를 읽기만 한다. 쓰기는 없다 — 이관은 소스를 건드리지 않는다."""

    def __init__(self, base_url: str, api_key: str, *, timeout: int = TIMEOUT) -> None:
        self._base = _http_url(base_url)
        self._key = api_key
        self._timeout = timeout

    def get(self, path: str, **params: str | int) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: str(v) for k, v in params.items()})
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        # 스킴은 `_http_url` 이 http/https 로 못 박았다 — 그래서 `urlopen` 이
        # 파일이나 다른 스킴을 열 길이 없다.
        request = urllib.request.Request(  # noqa: S310  # nosec B310
            url, headers={"X-Redmine-API-Key": self._key, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(  # noqa: S310  # nosec B310
                request, timeout=self._timeout
            ) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RedmineError(_explain(exc, path)) from None
        except urllib.error.URLError as exc:
            raise RedmineError(f"{self._base} 에 닿지 못했다 — {exc.reason}") from None
        if not isinstance(body, dict):
            raise RedmineError(f"{path}: 객체를 기대했는데 아니다")
        return body

    def paged(self, path: str, key: str, **params: str | int) -> list[dict[str, Any]]:
        """`total_count` 를 보고 끝까지 받는다."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = self.get(path, limit=PAGE, offset=offset, **params)
            rows = body.get(key, [])
            if not isinstance(rows, list):
                raise RedmineError(f"{path}: `{key}` 가 목록이 아니다")
            out.extend(r for r in rows if isinstance(r, dict))
            total = body.get("total_count")
            offset += PAGE
            if not isinstance(total, int) or offset >= total or not rows:
                return out


def _explain(exc: urllib.error.HTTPError, path: str) -> str:
    """왜 막혔는지 **고칠 수 있게** 말한다. 키는 절대 싣지 않는다."""
    if exc.code == 401:
        return f"{path}: 401 — API 키가 틀렸거나 REST API 가 꺼져 있다"
    if exc.code == 403:
        return f"{path}: 403 — 이 키에 권한이 없다"
    if exc.code == 404:
        return f"{path}: 404 — 그런 프로젝트가 없거나 이 키로는 안 보인다"
    return f"{path}: HTTP {exc.code}"


def _name(body: dict[str, Any], key: str) -> str:
    """`{"id": 1, "name": "Bug"}` 에서 이름만. **id 는 인스턴스마다 다르다.**"""
    value = body.get(key)
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if isinstance(name, str) else ""
    return ""


def _ref_id(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if isinstance(value, dict) and "id" in value:
        return str(value["id"])
    return ""


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def _body(body: dict[str, Any], key: str) -> str:
    """사람이 쓴 글. **줄 끝을 `\\n` 으로 통일한다.**

    Redmine 은 본문과 코멘트를 `\\r\\n` 으로 돌려준다(재어 봤다). 그대로 실으면
    JSON 안에 `\\r\\n` 이 흩어져 묶음을 사람이 읽기 나빠지고, 받는 쪽마다 같은
    정리를 다시 해야 한다. 마크다운에서 줄 끝은 옮길 내용이 아니라 **전송의
    흔적**이다.
    """
    return _text(body, key).replace("\r\n", "\n").replace("\r", "\n")


def _comments(detail: dict[str, Any]) -> list[Comment]:
    """`journals` 중 **말이 적힌 것**만 코멘트다.

    나머지는 필드가 바뀐 기록이다(상태 변경 등). 그것까지 코멘트로 옮기면
    빈 코멘트가 이슈마다 줄줄이 달린다 — 이력은 다음 조각에서 따로 본다.
    """
    out: list[Comment] = []
    journals = detail.get("journals", [])
    if not isinstance(journals, list):
        return out
    for entry in journals:
        if not isinstance(entry, dict):
            continue
        notes = _text(entry, "notes").strip()
        if not notes:
            continue
        out.append(
            Comment(
                source_id=str(entry.get("id", "")),
                body=_body(entry, "notes"),
                created_at=_text(entry, "created_on"),
                author=_ref_id(entry, "user"),
            )
        )
    return out


def _relations(detail: dict[str, Any], *, self_id: str) -> list[Relation]:
    """이 이슈에서 **나가는** 관계만 싣는다.

    Redmine 은 양쪽 이슈에 같은 관계를 다 보여 준다. 그대로 실으면 묶음에
    같은 관계가 두 번 들어오고, 받는 쪽이 그걸 두 줄로 만든다.
    """
    out: list[Relation] = []
    rows = detail.get("relations", [])
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("issue_id", "")) != self_id:
            continue
        out.append(
            Relation(kind=_text(row, "relation_type"), target=str(row.get("issue_to_id", "")))
        )
    return out


def _ratio(detail: dict[str, Any]) -> int:
    """`done_ratio`. 숫자가 아니면 0 으로 둔다.

    `bool` 을 따로 막는 이유는 파이썬에서 `True` 가 `int` 이기 때문이다 —
    묶음 포맷 쪽에서도 같은 구멍을 막았다.
    """
    value = detail.get("done_ratio")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _issue(detail: dict[str, Any]) -> Issue:
    return Issue(
        source_id=str(detail.get("id", "")),
        summary=_text(detail, "subject"),
        description=_body(detail, "description"),
        type=_name(detail, "tracker"),
        status=_name(detail, "status"),
        priority=_name(detail, "priority"),
        author=_ref_id(detail, "author"),
        assignee=_ref_id(detail, "assigned_to"),
        created_at=_text(detail, "created_on"),
        updated_at=_text(detail, "updated_on"),
        closed_at=_text(detail, "closed_on"),
        start_date=_text(detail, "start_date"),
        due_date=_text(detail, "due_date"),
        done_ratio=_ratio(detail),
        parent=_ref_id(detail, "parent"),
        relations=_relations(detail, self_id=str(detail.get("id", ""))),
        comments=_comments(detail),
    )


def _people(client: Client, *, log: Any) -> list[Person]:
    """사람 표. **메일이 잇는 열쇠다.**

    `/users.json` 은 관리자만 볼 수 있다. 관리자 키가 아니면 메일을 못 얻는데,
    그때 조용히 빈 표를 싣지 않는다 — 받는 쪽에서 "담당자가 아무도 안 붙었다"
    로 나타나고 이유를 모른다.
    """
    try:
        # **`status="*"` 가 핵심이다.** 기본값은 활동 중인 사람만 준다 —
        # 몇 년 굴린 Redmine 에서 잠긴 계정은 대개 퇴사자 전부이고, 그들이 쓴
        # 이슈가 작성자를 잃는다. 진짜 인스턴스에서 재어 확인했다: 한 명을
        # 잠그면 기본 질의에서 사라지고 `status=*` 에는 남는다.
        # (빈 문자열 `status=""` 는 422 다. 그것도 재어 봤다.)
        rows = client.paged("/users.json", "users", status="*")
    except RedmineError as exc:
        print(f"경고: 사람 목록을 못 읽었다 ({exc}).", file=log)
        print(
            "      메일이 없으면 받는 쪽이 사람을 잇지 못한다 — 관리자 키로 다시 돌리세요.",
            file=log,
        )
        return []
    people: list[Person] = []
    for row in rows:
        first = _text(row, "firstname")
        last = _text(row, "lastname")
        people.append(
            Person(
                source_id=str(row.get("id", "")),
                # Redmine 은 이름을 두 칸으로 들고 있다. 표시 순서는 설정이지만
                # 여기서는 이름 자체가 목적이 아니라 **사람을 알아보는 것**이다.
                name=f"{first} {last}".strip() or _text(row, "login"),
                email=_text(row, "mail"),
                login=_text(row, "login"),
                active=row.get("status") == 1,
            )
        )
    return people


def export_project(
    client: Client, project_key: str, *, base_url: str, log: Any = sys.stderr
) -> Archive:
    """프로젝트 하나를 묶음으로 만든다."""
    body = client.get(f"/projects/{urllib.parse.quote(project_key)}.json")
    project_body = body.get("project", {})
    if not isinstance(project_body, dict):
        raise RedmineError("프로젝트를 못 읽었다")
    project = Project(
        key=_text(project_body, "identifier") or project_key,
        name=_text(project_body, "name"),
        description=_text(project_body, "description"),
    )

    people = _people(client, log=log)

    # `status_id=*` 가 핵심이다. 없으면 **닫힌 이슈가 통째로 빠진다** — 그리고
    # 그 사실은 옮긴 쪽에서 한참 뒤에나 드러난다.
    listed = client.paged(
        "/issues.json", "issues", project_id=project.key, status_id="*", subproject_id="!*"
    )
    print(f"이슈 {len(listed)}개. 코멘트를 받으려고 하나씩 더 읽는다…", file=log)

    issues: list[Issue] = []
    for number, row in enumerate(listed, start=1):
        issue_id = row.get("id")
        detail_body = client.get(f"/issues/{issue_id}.json", include="journals,relations")
        detail = detail_body.get("issue", {})
        if not isinstance(detail, dict):
            raise RedmineError(f"이슈 {issue_id} 를 못 읽었다")
        issues.append(_issue(detail))
        if number % 50 == 0 or number == len(listed):
            print(f"  {number}/{len(listed)}", file=log)

    return Archive(
        manifest=Manifest(
            # **판은 비워 둔다.** Redmine 은 자기 판을 REST 로 안 알려 준다 —
            # `/admin/info` 는 HTML 이고 API 키로는 403 이다(재어 봤다). 매번
            # 실패할 요청을 보내느니 모른다고 두는 쪽이 정직하다.
            source=Source(kind="redmine", base_url=base_url),
            project=project,
            taken_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            adapter=ADAPTER,
        ),
        people=people,
        issues=issues,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ieum.migrate.redmine",
        description="Redmine 프로젝트 하나를 이관 묶음으로 뽑는다. 소스는 읽기만 한다.",
    )
    parser.add_argument("--url", required=True, help="Redmine 주소 (https://redmine.example.com)")
    parser.add_argument("--project", required=True, help="프로젝트 식별자 (identifier)")
    parser.add_argument("--out", required=True, help="만들 묶음 파일 경로")
    parser.add_argument(
        "--key",
        default="",
        help="API 키. 안 주면 REDMINE_API_KEY 를 읽는다 (그쪽을 권한다 — "
        "명령줄 인자는 프로세스 목록에 남는다)",
    )
    args = parser.parse_args(argv)

    api_key = args.key or os.environ.get("REDMINE_API_KEY", "")
    if not api_key:
        print("API 키가 없다. --key 나 REDMINE_API_KEY 를 주세요.", file=sys.stderr)
        return 2

    client = Client(args.url, api_key)
    try:
        archive = export_project(client, args.project, base_url=args.url.rstrip("/"))
    except RedmineError as exc:
        print(f"멈췄다: {exc}", file=sys.stderr)
        return 1

    with open(args.out, "wb") as handle:
        handle.write(write_archive(archive))

    comments = sum(len(i.comments) for i in archive.issues)
    print(
        f"{args.out} — 사람 {len(archive.people)}, 이슈 {len(archive.issues)}, 코멘트 {comments}",
        file=sys.stderr,
    )
    if not archive.people:
        print("사람 표가 비었다. 받는 쪽에서 작성자·담당자가 안 붙는다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
