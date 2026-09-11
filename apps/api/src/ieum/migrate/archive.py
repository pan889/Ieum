"""이관 묶음 — 소스와 Ieum 사이의 한 겹.

어댑터가 이것을 쓰고 임포터가 이것을 읽는다. 소스가 넷(Jira·Confluence·
Redmine·Zammad)인데 받는 쪽을 넷 만들지 않으려고 가운데에 둔 것이다.

## 왜 id 가 아니라 이름인가

진짜 Redmine 6 을 세워 확인한 것이다. 상태·우선순위·트래커의 **id 는 인스턴스
마다 다르다** — `priority_id: 3` 이 어느 인스턴스에서는 High 이고 다른 데서는
아무것도 아니다. 그래서 묶음에는 사람이 읽는 이름을 싣고, 그 이름을 Ieum 의
어휘에 맞추는 일은 받는 쪽이 한다.

## 왜 사람 표가 따로 있는가

같은 확인에서 나왔다. Redmine 이슈에 실려 오는 작성자·담당자는 **표시 이름뿐**
이다(`{"id": 1, "name": "Redmine Admin"}`). 그것만으로 우리 쪽 사람과 이으면
동명이인 하나에 남의 이슈가 된다. 어댑터가 사람 목록을 따로 읽어 **메일과 함께**
싣고, 잇는 일은 메일로 한다.

## 묶음 하나는 프로젝트 하나다

여러 프로젝트를 한 묶음에 담을 수도 있었지만 그러지 않았다. 이관은 되돌리기
번거로운 일이라 **한 번에 무엇이 들어오는지 사람이 셀 수 있어야** 하고, 프로젝트
단위면 하나 옮겨 확인하고 다음으로 갈 수 있다. 여러 개는 어댑터를 여러 번 돌린다.

    archive.zip
    ├── manifest.json   어느 소스의 무엇을 언제 뽑았는가
    ├── people.jsonl    사람 — 메일이 잇는 열쇠다
    └── issues.jsonl    이슈와 그 코멘트
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

#: 묶음 포맷의 판. 올라간 뒤에도 **옛 묶음을 계속 읽어야 한다** — 관리자가
#: 어제 뽑아 둔 파일이 오늘 판에서 안 읽히면 그건 우리 잘못이다.
FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
PEOPLE_NAME = "people.jsonl"
ISSUES_NAME = "issues.jsonl"

#: 사고로 올린 큰 파일이 워커를 잡지 않게. `wiki/portable.py` 와 같은 이유이고
#: 이관은 그보다 크므로 넉넉하다.
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ISSUES = 50_000
MAX_PEOPLE = 10_000
#: 한 줄이 이보다 길면 읽기를 멈춘다. 압축을 푸는 쪽에서 메모리를 다 쓰지 않게.
MAX_LINE_BYTES = 4 * 1024 * 1024

#: 아는 관계 종류. 모르는 것은 버리지 않고 **보고서에 남긴다**(받는 쪽에서).
RELATION_KINDS = ("relates", "duplicates", "blocks", "precedes", "copied_to")


class ArchiveError(ValueError):
    """묶음이 우리가 읽을 수 있는 모양이 아니다.

    메시지는 **고칠 수 있게** 쓴다 — 어느 파일의 몇 번째 줄인지까지.
    """


@dataclass(frozen=True, slots=True)
class Source:
    """어디서 뽑았는가. 멱등의 열쇠 절반이다."""

    kind: str
    """`redmine` 처럼 소스 종류. (kind, 원래 id) 가 이슈를 유일하게 가리킨다."""
    base_url: str = ""
    """원래 주소. 사람이 "이게 어느 서버 것이었지" 를 물을 때 쓴다."""
    version: str = ""


@dataclass(frozen=True, slots=True)
class Project:
    key: str
    """소스에서의 식별자(Redmine 의 identifier). 사람이 알아보는 것."""
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Manifest:
    source: Source
    project: Project
    taken_at: str
    """뽑은 시각(ISO-8601). 소스가 그 뒤로 바뀐 것은 안 넘어온다."""
    adapter: str = ""
    format: int = FORMAT_VERSION


@dataclass(frozen=True, slots=True)
class Person:
    source_id: str
    name: str
    email: str = ""
    """**빈 값일 수 있다.** 메일 없는 사람은 잇지 않고 보고서에 남긴다."""
    login: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class Comment:
    source_id: str
    body: str
    created_at: str
    author: str = ""
    """사람의 `source_id`. 빈 값이면 누가 썼는지 모르는 것으로 둔다."""


@dataclass(frozen=True, slots=True)
class Relation:
    kind: str
    target: str
    """상대 이슈의 `source_id`."""


@dataclass(frozen=True, slots=True)
class Issue:
    source_id: str
    summary: str
    description: str = ""
    #: 소스의 어휘 그대로. 받는 쪽이 자기 어휘로 옮긴다.
    type: str = ""
    status: str = ""
    priority: str = ""
    author: str = ""
    assignee: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    start_date: str = ""
    due_date: str = ""
    done_ratio: int = 0
    parent: str = ""
    labels: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Archive:
    manifest: Manifest
    people: list[Person]
    issues: list[Issue]


# ── 쓰기 ──────────────────────────────────────────────────────────


def _clean(value: Any) -> Any:
    """빈 것을 뺀 사전. 묶음을 사람이 열어 볼 수 있게 짧게 둔다."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if item in ("", [], {}, None):
                continue
            out[str(key)] = _clean(item)
        return out
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _issue_json(issue: Issue) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source_id": issue.source_id,
        "summary": issue.summary,
        "description": issue.description,
        "type": issue.type,
        "status": issue.status,
        "priority": issue.priority,
        "author": issue.author,
        "assignee": issue.assignee,
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
        "closed_at": issue.closed_at,
        "start_date": issue.start_date,
        "due_date": issue.due_date,
        "parent": issue.parent,
        "labels": issue.labels,
        "relations": [{"kind": r.kind, "target": r.target} for r in issue.relations],
        "comments": [
            {
                "source_id": c.source_id,
                "author": c.author,
                "body": c.body,
                "created_at": c.created_at,
            }
            for c in issue.comments
        ],
    }
    # `done_ratio` 는 0 이 뜻 있는 값이라 `_clean` 에 맡기지 않는다.
    body["done_ratio"] = issue.done_ratio
    return dict(_clean(body))


def write_archive(archive: Archive) -> bytes:
    """묶음을 만든다.

    줄 단위(JSONL)로 쓴다 — 이슈가 오만 개여도 한 줄씩 읽을 수 있고, 사람이
    `head` 로 들여다볼 수 있다.
    """
    manifest = {
        "format": archive.manifest.format,
        "source": {
            "kind": archive.manifest.source.kind,
            "base_url": archive.manifest.source.base_url,
            "version": archive.manifest.source.version,
        },
        "project": {
            "key": archive.manifest.project.key,
            "name": archive.manifest.project.name,
            "description": archive.manifest.project.description,
        },
        "taken_at": archive.manifest.taken_at,
        "adapter": archive.manifest.adapter,
        # 받는 쪽이 읽기 전에 규모를 알 수 있게. 미리 보기가 이것을 먼저 보여 준다.
        "counts": {
            "people": len(archive.people),
            "issues": len(archive.issues),
            "comments": sum(len(i.comments) for i in archive.issues),
        },
    }

    people_lines = "\n".join(
        json.dumps(
            _clean(
                {
                    "source_id": p.source_id,
                    "name": p.name,
                    "email": p.email,
                    "login": p.login,
                    "active": p.active,
                }
            ),
            ensure_ascii=False,
        )
        for p in archive.people
    )
    issue_lines = "\n".join(json.dumps(_issue_json(i), ensure_ascii=False) for i in archive.issues)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(PEOPLE_NAME, people_lines + ("\n" if people_lines else ""))
        zf.writestr(ISSUES_NAME, issue_lines + ("\n" if issue_lines else ""))
    return buffer.getvalue()


# ── 읽기 ──────────────────────────────────────────────────────────


def _need_str(body: dict[str, Any], key: str, *, where: str) -> str:
    value = body.get(key, "")
    if not isinstance(value, str):
        raise ArchiveError(f"{where}: `{key}` 는 문자열이라야 한다")
    return value


def _read_lines(raw: bytes, *, name: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) > MAX_LINE_BYTES:
            raise ArchiveError(f"{name}:{number} 줄이 너무 길다")
        if len(rows) >= limit:
            raise ArchiveError(f"{name}: 줄이 {limit}개를 넘는다")
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"{name}:{number} JSON 이 아니다 — {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ArchiveError(f"{name}:{number} 객체 하나여야 한다")
        rows.append(parsed)
    return rows


def _person(body: dict[str, Any], *, where: str) -> Person:
    source_id = _need_str(body, "source_id", where=where)
    if not source_id:
        raise ArchiveError(f"{where}: `source_id` 가 비었다")
    active = body.get("active", True)
    return Person(
        source_id=source_id,
        name=_need_str(body, "name", where=where),
        email=_need_str(body, "email", where=where),
        login=_need_str(body, "login", where=where),
        active=bool(active),
    )


def _relations(body: dict[str, Any], *, where: str) -> list[Relation]:
    raw = body.get("relations", [])
    if not isinstance(raw, list):
        raise ArchiveError(f"{where}: `relations` 는 목록이라야 한다")
    out: list[Relation] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ArchiveError(f"{where}: `relations` 항목이 객체가 아니다")
        # **모르는 종류를 여기서 막지 않는다.** 소스마다 관계 어휘가 다르고,
        # 여기서 거절하면 묶음 하나가 통째로 안 들어온다. 받는 쪽이 옮길 수
        # 없는 것을 보고서에 남긴다.
        out.append(
            Relation(
                kind=_need_str(item, "kind", where=where),
                target=_need_str(item, "target", where=where),
            )
        )
    return out


def _comments(body: dict[str, Any], *, where: str) -> list[Comment]:
    raw = body.get("comments", [])
    if not isinstance(raw, list):
        raise ArchiveError(f"{where}: `comments` 는 목록이라야 한다")
    out: list[Comment] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ArchiveError(f"{where}: `comments` 항목이 객체가 아니다")
        out.append(
            Comment(
                source_id=_need_str(item, "source_id", where=where),
                body=_need_str(item, "body", where=where),
                created_at=_need_str(item, "created_at", where=where),
                author=_need_str(item, "author", where=where),
            )
        )
    return out


def _issue(body: dict[str, Any], *, where: str) -> Issue:
    source_id = _need_str(body, "source_id", where=where)
    if not source_id:
        raise ArchiveError(f"{where}: `source_id` 가 비었다")
    summary = _need_str(body, "summary", where=where)
    if not summary:
        raise ArchiveError(f"{where}: `summary` 가 비었다")
    done = body.get("done_ratio", 0)
    if not isinstance(done, int) or isinstance(done, bool):
        raise ArchiveError(f"{where}: `done_ratio` 는 정수라야 한다")
    labels = body.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ArchiveError(f"{where}: `labels` 는 문자열 목록이라야 한다")
    return Issue(
        source_id=source_id,
        summary=summary,
        description=_need_str(body, "description", where=where),
        type=_need_str(body, "type", where=where),
        status=_need_str(body, "status", where=where),
        priority=_need_str(body, "priority", where=where),
        author=_need_str(body, "author", where=where),
        assignee=_need_str(body, "assignee", where=where),
        created_at=_need_str(body, "created_at", where=where),
        updated_at=_need_str(body, "updated_at", where=where),
        closed_at=_need_str(body, "closed_at", where=where),
        start_date=_need_str(body, "start_date", where=where),
        due_date=_need_str(body, "due_date", where=where),
        done_ratio=done,
        parent=_need_str(body, "parent", where=where),
        labels=list(labels),
        relations=_relations(body, where=where),
        comments=_comments(body, where=where),
    )


def _manifest(body: dict[str, Any]) -> Manifest:
    where = MANIFEST_NAME
    version = body.get("format")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ArchiveError(f"{where}: `format` 이 없다 — 이관 묶음이 아닌 것 같다")
    if version > FORMAT_VERSION:
        # **앞선 판은 거절한다.** 모르는 필드를 조용히 버리면 사람은 다 들어온
        # 줄 안다.
        raise ArchiveError(
            f"{where}: 이 묶음은 포맷 {version} 인데 이 판은 {FORMAT_VERSION} 까지 읽는다"
        )
    source = body.get("source", {})
    project = body.get("project", {})
    if not isinstance(source, dict) or not isinstance(project, dict):
        raise ArchiveError(f"{where}: `source` 와 `project` 는 객체라야 한다")
    kind = _need_str(source, "kind", where=where)
    if not kind:
        raise ArchiveError(f"{where}: `source.kind` 가 비었다")
    key = _need_str(project, "key", where=where)
    if not key:
        raise ArchiveError(f"{where}: `project.key` 가 비었다")
    return Manifest(
        source=Source(
            kind=kind,
            base_url=_need_str(source, "base_url", where=where),
            version=_need_str(source, "version", where=where),
        ),
        project=Project(
            key=key,
            name=_need_str(project, "name", where=where) or key,
            description=_need_str(project, "description", where=where),
        ),
        taken_at=_need_str(body, "taken_at", where=where),
        adapter=_need_str(body, "adapter", where=where),
        format=version,
    )


def read_archive(data: bytes) -> Archive:
    """묶음을 읽는다. 못 읽으면 **어디가 문제인지** 말하고 멈춘다."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"묶음이 너무 크다 ({len(data)} 바이트)")
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("ZIP 이 아니다") from exc

    with zf:
        names = set(zf.namelist())
        missing = [n for n in (MANIFEST_NAME, PEOPLE_NAME, ISSUES_NAME) if n not in names]
        if missing:
            raise ArchiveError(f"묶음에 {', '.join(missing)} 가 없다")
        # 압축을 풀었을 때의 크기를 먼저 본다 — 작은 ZIP 이 기가바이트로 부푸는
        # 것(zip bomb)을 열기 전에 막는다.
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_ARCHIVE_BYTES:
            raise ArchiveError(f"압축을 풀면 너무 크다 ({total} 바이트)")

        try:
            manifest_body = json.loads(zf.read(MANIFEST_NAME))
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"{MANIFEST_NAME}: JSON 이 아니다 — {exc.msg}") from exc
        if not isinstance(manifest_body, dict):
            raise ArchiveError(f"{MANIFEST_NAME}: 객체 하나여야 한다")
        manifest = _manifest(manifest_body)

        people_rows = _read_lines(zf.read(PEOPLE_NAME), name=PEOPLE_NAME, limit=MAX_PEOPLE)
        issue_rows = _read_lines(zf.read(ISSUES_NAME), name=ISSUES_NAME, limit=MAX_ISSUES)

    people = [
        _person(row, where=f"{PEOPLE_NAME}:{n}") for n, row in enumerate(people_rows, start=1)
    ]
    issues = [_issue(row, where=f"{ISSUES_NAME}:{n}") for n, row in enumerate(issue_rows, start=1)]

    seen: set[str] = set()
    for issue in issues:
        if issue.source_id in seen:
            raise ArchiveError(f"{ISSUES_NAME}: `source_id` 가 겹친다 — {issue.source_id}")
        seen.add(issue.source_id)

    return Archive(manifest=manifest, people=people, issues=issues)
