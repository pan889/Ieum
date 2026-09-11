"""Redmine 어댑터.

**여기 쓰이는 payload 는 지어낸 것이 아니다.** `redmine:6-alpine` 을 띄워
프로젝트·이슈·코멘트·부모자식·관계를 넣고 REST 로 받아 그대로 떠 둔 것이다
(`fixtures/redmine/`). 손으로 지으면 내가 아는 모양만 짓게 되고, 그러면
**내가 모르는 자리에서만** 틀린다.

실제로 그 payload 안에 내가 안 지었을 경계가 둘 있었다:

- 말이 안 적힌 journal (부모를 지정한 기록). 코멘트로 세면 빈 코멘트가 달린다.
- 거울로 보이는 관계. 양쪽 이슈가 같은 관계를 다 들고 있어서, 그대로 실으면
  묶음에 두 번 들어온다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ieum.migrate.redmine import _issue, _people

FIXTURES = Path(__file__).parent / "fixtures" / "redmine"


def _load(name: str) -> dict[str, Any]:
    body = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


def _detail(name: str) -> dict[str, Any]:
    issue = _load(name)["issue"]
    assert isinstance(issue, dict)
    return issue


class TestItCarriesNamesNotIds:
    """상태·우선순위·트래커 **id 는 인스턴스마다 다르다.** 이름을 실어야 한다."""

    def test_the_vocabulary_comes_across_as_words(self) -> None:
        issue = _issue(_detail("issue-2"))
        assert (issue.type, issue.status) == ("Bug", "New")
        assert issue.priority == "Low"

    def test_people_stay_as_ids_because_names_are_not_unique(self) -> None:
        """사람만은 id 로 싣는다 — 이름은 겹치고, 메일은 사람 표에 있다."""
        issue = _issue(_detail("issue-2"))
        assert issue.author == "1"
        assert issue.assignee == "5"


class TestComments:
    def test_only_journals_with_words_are_comments(self) -> None:
        """**이 시험이 이 파일의 이유다.**

        `issue-2` 의 journal 넷 중 둘에만 말이 적혀 있다. 나머지 둘은 필드가
        바뀐 기록이다. 넷을 다 코멘트로 옮기면 빈 코멘트가 줄줄이 달린다.
        """
        detail = _detail("issue-2")
        assert len(detail["journals"]) == 4  # 실물이 이렇다
        issue = _issue(detail)
        assert len(issue.comments) == 2
        assert all(c.body.strip() for c in issue.comments)

    def test_an_issue_whose_only_journal_is_a_field_change_has_none(self) -> None:
        detail = _detail("issue-4")
        assert len(detail["journals"]) == 1
        assert _issue(detail).comments == []

    def test_line_endings_come_out_as_lf(self) -> None:
        """Redmine 은 `\\r\\n` 으로 준다. 줄 끝은 옮길 내용이 아니라 전송의 흔적이다."""
        raw = _detail("issue-2")["description"]
        assert "\r\n" in raw, "픽스처가 실물이 아니게 됐다"
        assert "\r" not in _issue(_detail("issue-2")).description


class TestRelations:
    def test_only_the_outgoing_side_is_carried(self) -> None:
        """Redmine 은 양쪽 이슈에 같은 관계를 다 보여 준다.

        `issue-3` 이 들고 있는 관계는 `issue_id: 2` — 남의 것이다. 그대로 실으면
        묶음에 같은 관계가 두 번 들어오고 받는 쪽이 두 줄로 만든다.
        """
        assert _detail("issue-3")["relations"], "픽스처에 관계가 있어야 이 시험이 뜻이 있다"
        assert _issue(_detail("issue-3")).relations == []

        outgoing = _issue(_detail("issue-2")).relations
        assert [(r.kind, r.target) for r in outgoing] == [("relates", "3")]


class TestParents:
    def test_the_parent_comes_across_as_a_source_id(self) -> None:
        assert _issue(_detail("issue-4")).parent == "2"

    def test_no_parent_is_empty_not_a_guess(self) -> None:
        assert _issue(_detail("issue-2")).parent == ""


class _Recorded:
    """떠 둔 `users.json` 을 그대로 돌려주는 가짜 클라이언트."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def paged(self, path: str, key: str, **params: str | int) -> list[dict[str, Any]]:
        # **어댑터가 `status="*"` 로 부르는지 여기서 붙잡는다.** 기본값으로
        # 부르면 잠긴 사람이 조용히 빠지고, 그건 퇴사자 전부를 잃는다는 뜻이다.
        assert params.get("status") == "*", f"잠긴 사람이 빠진다 — status={params.get('status')!r}"
        rows = self._body[key]
        assert isinstance(rows, list)
        return [r for r in rows if isinstance(r, dict)]


class TestPeople:
    def test_locked_people_come_across_marked_inactive(self) -> None:
        """진짜 인스턴스에서 한 명을 잠그고 재어 봤다: 기본 질의에서는 사라지고
        `status=*` 에는 남는다. 몇 년 굴린 Redmine 에서 잠긴 계정은 대개
        **퇴사자 전부**이고, 그들이 쓴 이슈가 작성자를 잃는다."""
        people = _people(_Recorded(_load("users")), log=None)  # type: ignore[arg-type]
        by_login = {p.login: p for p in people}
        assert set(by_login) == {"admin", "hana", "dubi"}
        assert by_login["dubi"].active is False
        assert by_login["hana"].active is True

    def test_the_mail_is_carried_because_that_is_what_joins_people(self) -> None:
        people = _people(_Recorded(_load("users")), log=None)  # type: ignore[arg-type]
        assert {p.email for p in people} == {
            "admin@example.net",
            "hana@example.com",
            "dubi@example.com",
        }

    def test_names_survive_being_split_in_two(self) -> None:
        """Redmine 은 이름을 두 칸으로 들고 있다. 한글도 그대로 와야 한다."""
        people = _people(_Recorded(_load("users")), log=None)  # type: ignore[arg-type]
        assert {p.name for p in people} >= {"하나 김"}
