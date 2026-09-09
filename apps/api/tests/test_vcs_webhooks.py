"""코드 호스트가 보낸 것을 읽는 자리 (feature-map A22).

HTTP 를 안 보는 순수 함수들이라 **페이로드 견본으로 값을 붙잡을 수 있다.**
그게 이 파일의 이유다: 호스트가 필드 하나를 바꿨을 때 어디가 깨지는지가
한곳에 모이고, 서명 확인이 실제로 거절하는지를 견본으로 확인할 수 있다.

여기서 지키는 약속:

- **서명이 틀리면 거절한다.** 몸이 한 글자 바뀌어도, 키가 달라도.
- **이메일을 이슈 이력에 적지 않는다.** 커밋 author 에 이메일만 있으면
  이름을 비운다 — 남의 주소를 우리 화면에 옮기는 일이 된다.
- **큰 푸시는 뒤쪽을 남긴다.** 최신 커밋이 사람이 찾는 것이다.
- **시각이 없으면 지금으로 둔다.** 비우면 목록 정렬이 무너진다.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from ieum.core.time import utcnow
from ieum.modules.vcs.webhooks import (
    MAX_CHANGES,
    parse_github,
    parse_gitlab,
    verify_github,
    verify_gitlab,
)

SECRET = "s3cret-webhook-key"


def _signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _commit(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "a" * 40,
        "message": "ENG-12 로그인 화면을 고친다\n\n자세한 내용은 본문에.",
        "url": "https://github.com/acme/web/commit/" + "a" * 40,
        "timestamp": "2026-09-01T10:00:00+09:00",
        "author": {"username": "sujin", "name": "김수진", "email": "sujin@example.com"},
    }
    base.update(over)
    return base


class TestGitHubSignature:
    def test_a_correct_signature_passes(self) -> None:
        body = b'{"zen":"Keep it logically awesome."}'
        assert verify_github(body=body, secret=SECRET, header=_signature(body))

    def test_a_changed_body_fails(self) -> None:
        """**서명은 몸에 붙는다.** 한 글자만 바뀌어도 어긋난다."""
        body = b'{"ref":"refs/heads/main"}'
        header = _signature(body)
        assert not verify_github(body=body + b" ", secret=SECRET, header=header)

    def test_another_secret_fails(self) -> None:
        body = b"{}"
        assert not verify_github(body=body, secret="other-key", header=_signature(body))

    def test_a_missing_header_fails(self) -> None:
        assert not verify_github(body=b"{}", secret=SECRET, header=None)

    def test_a_header_without_the_algorithm_fails(self) -> None:
        """`sha256=` 없이 온 것은 우리가 아는 형식이 아니다. 통과시키면
        `sha1=` 시절의 서명도 받아들이게 된다."""
        raw = hmac.new(SECRET.encode(), b"{}", hashlib.sha256).hexdigest()
        assert not verify_github(body=b"{}", secret=SECRET, header=raw)

    def test_an_empty_header_fails(self) -> None:
        assert not verify_github(body=b"{}", secret=SECRET, header="")


class TestGitLabToken:
    """**서명이 아니라 공유 토큰이다.** 몸을 보호하지 못한다 — GitLab 이
    그것만 보내므로 우리가 고를 수 있는 것이 아니다."""

    def test_the_exact_token_passes(self) -> None:
        assert verify_gitlab(secret=SECRET, header=SECRET)

    def test_another_token_fails(self) -> None:
        assert not verify_gitlab(secret=SECRET, header=SECRET + "x")

    def test_a_missing_token_fails(self) -> None:
        assert not verify_gitlab(secret=SECRET, header=None)

    def test_an_empty_token_fails(self) -> None:
        assert not verify_gitlab(secret=SECRET, header="")


class TestGitHubPush:
    def test_each_commit_becomes_one_change(self) -> None:
        changes = parse_github("push", {"commits": [_commit(), _commit(id="b" * 40)]})
        assert [c.kind for c in changes] == ["commit", "commit"]
        assert [c.ref for c in changes] == ["a" * 40, "b" * 40]

    def test_the_title_is_the_first_line_and_the_text_is_the_whole_message(self) -> None:
        """제목에 본문까지 넣으면 목록이 안 읽힌다. 키를 찾을 때는 전문을 본다 —
        `fixes ENG-12` 를 본문에 적는 팀이 있다."""
        (change,) = parse_github("push", {"commits": [_commit()]})
        assert change.title == "ENG-12 로그인 화면을 고친다"
        assert "자세한 내용은 본문에." in change.text

    def test_the_author_prefers_the_account_name(self) -> None:
        (change,) = parse_github("push", {"commits": [_commit()]})
        assert change.author == "sujin"

    def test_the_display_name_is_the_fallback(self) -> None:
        commit = _commit(author={"name": "김수진", "email": "sujin@example.com"})
        (change,) = parse_github("push", {"commits": [commit]})
        assert change.author == "김수진"

    def test_an_email_only_author_stays_empty(self) -> None:
        """**이메일을 쓰지 않는다.** 이슈 이력에 남의 주소를 적는 일이 된다."""
        commit = _commit(author={"email": "sujin@example.com"})
        (change,) = parse_github("push", {"commits": [commit]})
        assert change.author is None

    def test_a_commit_without_a_sha_is_skipped(self) -> None:
        changes = parse_github("push", {"commits": [_commit(id=""), _commit(id="c" * 40)]})
        assert [c.ref for c in changes] == ["c" * 40]

    def test_junk_in_the_commit_list_is_skipped(self) -> None:
        """호스트가 아니라 아무나 보낼 수 있는 몸이다(서명은 이 함수 밖에서
        본다). 목록 안에 문자열이 섞여 와도 터지지 않아야 한다."""
        changes = parse_github("push", {"commits": ["nope", None, _commit()]})
        assert [c.ref for c in changes] == ["a" * 40]

    def test_a_huge_push_keeps_the_last_commits(self) -> None:
        """브랜치를 처음 올리면 수천 개가 온다. **뒤쪽**을 남긴다 — 최신
        커밋이 사람이 찾는 것이다."""
        commits = [_commit(id=f"{index:040d}") for index in range(MAX_CHANGES + 30)]
        changes = parse_github("push", {"commits": commits})
        assert len(changes) == MAX_CHANGES
        assert changes[-1].ref == f"{MAX_CHANGES + 29:040d}"
        assert changes[0].ref == f"{30:040d}"

    def test_an_empty_push_is_no_changes(self) -> None:
        assert parse_github("push", {"commits": []}) == []
        assert parse_github("push", {}) == []


class TestGitHubPullRequest:
    def _payload(self, **over: Any) -> dict[str, Any]:
        request: dict[str, Any] = {
            "number": 41,
            "title": "로그인 화면 정리",
            "body": "fixes ENG-12",
            "html_url": "https://github.com/acme/web/pull/41",
            "head": {"ref": "feature/ENG-13-login"},
            "user": {"login": "sujin"},
            "updated_at": "2026-09-01T02:00:00Z",
        }
        request.update(over)
        return {"pull_request": request}

    def test_the_number_is_the_reference(self) -> None:
        (change,) = parse_github("pull_request", self._payload())
        assert (change.kind, change.ref) == ("pull_request", "41")
        assert change.url.endswith("/pull/41")
        assert change.author == "sujin"

    def test_the_branch_name_is_searched_too(self) -> None:
        """`feature/ENG-13-...` 만 있고 제목에는 키를 안 쓰는 팀이 있다."""
        (change,) = parse_github("pull_request", self._payload())
        assert "ENG-13" in change.text
        assert "ENG-12" in change.text

    def test_a_payload_without_a_number_is_no_change(self) -> None:
        assert parse_github("pull_request", self._payload(number=None)) == []

    def test_a_payload_without_the_object_is_no_change(self) -> None:
        assert parse_github("pull_request", {}) == []
        assert parse_github("pull_request", {"pull_request": "nope"}) == []

    def test_the_created_time_is_the_fallback(self) -> None:
        payload = self._payload(updated_at=None, created_at="2026-08-30T00:00:00Z")
        (change,) = parse_github("pull_request", payload)
        assert change.happened_at == datetime(2026, 8, 30, tzinfo=UTC)


class TestGitLab:
    def test_a_push_hook_becomes_commits(self) -> None:
        payload = {
            "commits": [
                {
                    "id": "d" * 40,
                    "message": "ENG-12 고친다",
                    "url": "https://gitlab.example.com/acme/web/-/commit/" + "d" * 40,
                    "timestamp": "2026-09-01T10:00:00+09:00",
                    "author": {"name": "김수진", "email": "sujin@example.com"},
                }
            ]
        }
        (change,) = parse_gitlab("Push Hook", payload)
        assert (change.kind, change.ref, change.author) == ("commit", "d" * 40, "김수진")

    def test_a_merge_request_uses_the_project_local_number(self) -> None:
        """`iid` 다. `id` 는 인스턴스 전역 번호라 저장소 주소와 안 맞는다."""
        payload = {
            "object_attributes": {
                "id": 90210,
                "iid": 7,
                "title": "로그인 정리",
                "description": "closes ENG-12",
                "source_branch": "eng-12-login",
                "url": "https://gitlab.example.com/acme/web/-/merge_requests/7",
                "updated_at": "2026-09-01 02:00:00 UTC",
            },
            "user": {"username": "sujin"},
        }
        (change,) = parse_gitlab("Merge Request Hook", payload)
        assert (change.kind, change.ref) == ("pull_request", "7")
        assert change.author == "sujin"
        assert "ENG-12" in change.text

    def test_a_merge_request_without_attributes_is_no_change(self) -> None:
        assert parse_gitlab("Merge Request Hook", {}) == []

    def test_an_unreadable_timestamp_falls_back_to_now(self) -> None:
        """비워 두면 목록 정렬이 무너지고, 옛 시각으로 두면 방금 온 커밋이
        맨 아래로 간다."""
        payload = {
            "object_attributes": {"iid": 8, "title": "x", "updated_at": "어제쯤"},
        }
        (change,) = parse_gitlab("Merge Request Hook", payload)
        assert abs((utcnow() - change.happened_at).total_seconds()) < 10

    def test_a_naive_timestamp_gets_a_timezone(self) -> None:
        """시간대 없는 값을 그대로 두면 DB 에 넣을 때 터진다."""
        payload = {
            "object_attributes": {"iid": 9, "title": "x", "updated_at": "2026-09-01T10:00:00"}
        }
        (change,) = parse_gitlab("Merge Request Hook", payload)
        assert change.happened_at.tzinfo is not None


class TestOtherEvents:
    def test_events_we_do_not_read_are_no_changes(self) -> None:
        """`ping`·`issues`·`star` 같은 것들이다. 오류로 만들지 않는다 —
        코드 호스트는 4xx 를 재전송으로 갚는다."""
        assert parse_github("ping", {"zen": "Keep it logically awesome."}) == []
        assert parse_github("issues", {"action": "opened"}) == []
        assert parse_gitlab("Note Hook", {"object_attributes": {"iid": 1}}) == []
