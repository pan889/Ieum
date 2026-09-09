"""코드 호스트가 보내는 것을 읽는다 (A22, M6).

## 서명 확인이 이 파일의 이유다

이 엔드포인트는 **인증 없이** 열려 있다. 코드 호스트는 우리 액세스 토큰을
갖고 있지 않으므로, 그 몸이 진짜인지는 서명으로만 안다. 확인이 없으면 주소를
아는 누구나 이슈에 커밋 제목과 주소를 남길 수 있다 — 그리고 그건 이슈 이력에
영구히 남는다.

두 호스트가 서로 다르게 한다:

- **GitHub**: `X-Hub-Signature-256: sha256=<본문의 HMAC-SHA256>`. 몸을 키로
  서명한 값이라 몸이 한 글자라도 바뀌면 어긋난다.
- **GitLab**: `X-Gitlab-Token: <시크릿 그대로>`. 서명이 아니라 **공유 토큰**
  이다. 몸을 보호하지 못하지만 GitLab 이 그것만 보낸다 — 우리가 고를 수 있는
  것이 아니다. 문서에 그렇게 적어 둔다.

둘 다 **상수 시간 비교**를 쓴다. `==` 로 비교하면 앞자리부터 맞춰 가며 키를
알아낼 수 있다.

## 파싱은 순수 함수다

호스트가 보내는 JSON 에서 우리가 쓸 것만 뽑는다. HTTP 를 안 보므로 페이로드
견본으로 시험할 수 있고, 호스트가 필드를 하나 바꿨을 때 어디가 깨지는지가
한곳에 모인다.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ieum.core.time import utcnow

#: 한 번에 받아들일 변경 수. 큰 푸시(브랜치 하나를 처음 올릴 때)가 수천 개의
#: 커밋을 담아 오는데, 그걸 다 훑으면 한 요청이 몇 분을 쓴다.
#:
#: 넘치면 **앞쪽을 버리고 뒤쪽을 쓴다** — 최신 커밋이 사람이 찾는 것이다.
MAX_CHANGES = 100


@dataclass(frozen=True, slots=True)
class Change:
    """이슈에 붙일 변경 하나."""

    kind: str
    #: 커밋이면 SHA, PR 이면 번호(문자열).
    ref: str
    title: str
    url: str
    author: str | None
    happened_at: datetime
    #: 이슈 키를 찾을 글. 커밋이면 메시지 전체, PR 이면 제목·본문·브랜치다.
    text: str


def verify_github(*, body: bytes, secret: str, header: str | None) -> bool:
    """`X-Hub-Signature-256` 을 확인한다."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def verify_gitlab(*, secret: str, header: str | None) -> bool:
    """`X-Gitlab-Token` 을 확인한다.

    **서명이 아니라 공유 토큰이다.** 몸을 보호하지 못한다 — 토큰을 아는 쪽은
    아무 몸이나 보낼 수 있다. GitLab 이 그것만 보내므로 우리가 고를 수 있는
    것이 아니고, 그래서 토큰을 시크릿과 같은 급으로 다룬다(암호화 저장, 한 번
    만 보여 주기).
    """
    return bool(header) and hmac.compare_digest(secret, header or "")


def parse_github(event: str, payload: dict[str, Any]) -> list[Change]:
    """GitHub 의 `push`·`pull_request` 를 읽는다. 나머지는 빈 목록이다."""
    if event == "push":
        return _github_push(payload)
    if event == "pull_request":
        return _github_pull_request(payload)
    return []


def _github_push(payload: dict[str, Any]) -> list[Change]:
    commits = _tail(payload.get("commits") or [])
    out: list[Change] = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        sha = str(commit.get("id") or "")
        message = str(commit.get("message") or "")
        if not sha:
            continue
        out.append(
            Change(
                kind="commit",
                ref=sha,
                # 제목은 첫 줄이다. 본문까지 넣으면 목록이 안 읽힌다.
                title=_first_line(message),
                url=str(commit.get("url") or ""),
                author=_github_author(commit),
                happened_at=_timestamp(commit.get("timestamp")),
                text=message,
            )
        )
    return out


def _github_pull_request(payload: dict[str, Any]) -> list[Change]:
    request = payload.get("pull_request")
    if not isinstance(request, dict):
        return []
    number = request.get("number")
    if number is None:
        return []
    head = request.get("head") if isinstance(request.get("head"), dict) else {}
    branch = str((head or {}).get("ref") or "")
    body = str(request.get("body") or "")
    title = str(request.get("title") or "")
    user = request.get("user") if isinstance(request.get("user"), dict) else {}
    return [
        Change(
            kind="pull_request",
            ref=str(number),
            title=title,
            url=str(request.get("html_url") or ""),
            author=str((user or {}).get("login") or "") or None,
            happened_at=_timestamp(request.get("updated_at") or request.get("created_at")),
            # **브랜치 이름도 본다.** `feature/ENG-12-...` 만 있고 제목에는
            # 키를 안 쓰는 팀이 있다.
            text=f"{title}\n{body}\n{branch}",
        )
    ]


def parse_gitlab(event: str, payload: dict[str, Any]) -> list[Change]:
    """GitLab 의 `Push Hook`·`Merge Request Hook` 을 읽는다."""
    if event == "Push Hook":
        return _gitlab_push(payload)
    if event == "Merge Request Hook":
        return _gitlab_merge_request(payload)
    return []


def _gitlab_push(payload: dict[str, Any]) -> list[Change]:
    out: list[Change] = []
    for commit in _tail(payload.get("commits") or []):
        if not isinstance(commit, dict):
            continue
        sha = str(commit.get("id") or "")
        message = str(commit.get("message") or "")
        if not sha:
            continue
        author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
        out.append(
            Change(
                kind="commit",
                ref=sha,
                title=_first_line(message),
                url=str(commit.get("url") or ""),
                author=str((author or {}).get("name") or "") or None,
                happened_at=_timestamp(commit.get("timestamp")),
                text=message,
            )
        )
    return out


def _gitlab_merge_request(payload: dict[str, Any]) -> list[Change]:
    attrs = payload.get("object_attributes")
    if not isinstance(attrs, dict):
        return []
    iid = attrs.get("iid")
    if iid is None:
        return []
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    title = str(attrs.get("title") or "")
    body = str(attrs.get("description") or "")
    branch = str(attrs.get("source_branch") or "")
    return [
        Change(
            kind="pull_request",
            ref=str(iid),
            title=title,
            url=str(attrs.get("url") or ""),
            author=str((user or {}).get("username") or "") or None,
            happened_at=_timestamp(attrs.get("updated_at") or attrs.get("created_at")),
            text=f"{title}\n{body}\n{branch}",
        )
    ]


def _tail(items: Sequence[Any]) -> Sequence[Any]:
    """상한을 넘으면 **뒤쪽**을 쓴다. 최신 커밋이 사람이 찾는 것이다."""
    return items[-MAX_CHANGES:] if len(items) > MAX_CHANGES else items


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:500]


def _github_author(commit: dict[str, Any]) -> str | None:
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    # `username` 이 있으면 그것, 없으면 표시 이름. 둘 다 없으면 비운다 —
    # 이메일을 쓰지 않는다: 이슈 이력에 남의 주소를 적는 일이 된다.
    for field in ("username", "name"):
        value = str((author or {}).get(field) or "")
        if value:
            return value[:200]
    return None


def _timestamp(raw: Any) -> datetime:
    """호스트가 준 시각. 없거나 못 읽으면 **지금**으로 둔다.

    지금으로 두는 것이 맞는 이유: 이 값은 목록 정렬에 쓰인다. 비워 두면
    정렬이 무너지고, 옛 시각으로 두면 방금 온 커밋이 목록 맨 아래로 간다.
    """
    if not isinstance(raw, str) or not raw:
        return utcnow()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utcnow().tzinfo)


__all__ = [
    "MAX_CHANGES",
    "Change",
    "parse_github",
    "parse_gitlab",
    "verify_github",
    "verify_gitlab",
]
