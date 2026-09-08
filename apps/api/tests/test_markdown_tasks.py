"""본문의 태스크 리스트 파서 (feature-map B12).

붙잡는 것 — 전부 **조용히 틀리는** 종류다:

- **코드 블록 안의 `- [ ]` 는 태스크가 아니다.** 문법을 설명해 둔 문서 하나가
  모든 사람의 "내 할 일" 에 예시를 뿌린다.
- **줄 번호가 태스크의 신원이다.** 몇 번째냐로 세면 중첩 목록에서 어긋날 수
  있고, 어긋난 채로 체크하면 **다른 줄이 바뀐다.**
- **체크는 마커 두 글자만 바꾼다.** 다시 직렬화하면 사람이 쓴 공백·불릿
  종류가 조용히 정규화되고, 그 diff 가 무엇이 바뀌었나를 가린다.
- **못 읽는 기한은 글자로 남는다.** 조용히 버리면 사람은 기한을 적었다고
  믿고 아무 일도 안 일어난다.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from ieum.core.markdown.tasks import MAX_TASKS, parse_tasks, set_done

ALICE = UUID("0198f000-0000-7000-8000-000000000001")
BOB = UUID("0198f000-0000-7000-8000-000000000002")


def _mention(user_id: UUID, name: str = "Alice") -> str:
    return f"[@{name}](user:{user_id})"


class TestWhatCountsAsATask:
    def test_it_reads_open_and_done(self) -> None:
        tasks = parse_tasks("- [ ] 안 끝난 일\n- [x] 끝난 일\n")
        assert [(t.line, t.done, t.text) for t in tasks] == [
            (0, False, "안 끝난 일"),
            (1, True, "끝난 일"),
        ]

    def test_an_uppercase_x_is_done_too(self) -> None:
        # 손으로 쓴 문서에는 `[X]` 가 흔하다. 그것을 미완료로 읽으면 끝낸 일이
        # 영원히 남의 할 일 목록에 뜬다.
        assert parse_tasks("- [X] 끝났다\n")[0].done is True

    def test_a_plain_bullet_is_not_a_task(self) -> None:
        assert parse_tasks("- 그냥 항목\n") == []

    def test_a_code_block_holds_no_tasks(self) -> None:
        """**이 시험이 이 파일의 이유다.**

        정규식으로 찾으면 문법을 설명해 둔 문서가 모든 사람의 "내 할 일" 에
        예시를 뿌린다. 그리고 그건 버그로 안 보인다 — 그냥 이상한 항목이
        하나 늘어난 것으로 보인다.
        """
        body = "실제 태스크:\n\n- [ ] 진짜 일\n\n쓰는 법:\n\n```\n- [ ] 예시\n- [x] 예시2\n```\n"
        tasks = parse_tasks(body)
        assert [t.text for t in tasks] == ["진짜 일"]

    def test_an_indented_code_block_holds_no_tasks_either(self) -> None:
        body = "글:\n\n    - [ ] 네 칸 들여쓴 코드\n\n- [ ] 진짜\n"
        assert [t.text for t in parse_tasks(body)] == ["진짜"]

    def test_nested_tasks_are_tasks(self) -> None:
        body = "- [ ] 큰 일\n  - [ ] 작은 일\n  - [x] 끝난 작은 일\n"
        tasks = parse_tasks(body)
        assert [(t.line, t.text) for t in tasks] == [
            (0, "큰 일"),
            (1, "작은 일"),
            (2, "끝난 작은 일"),
        ]

    def test_other_bullet_characters_work(self) -> None:
        assert len(parse_tasks("* [ ] 별\n+ [ ] 더하기\n- [ ] 하이픈\n")) == 3

    def test_a_marker_with_no_text_is_not_a_task(self) -> None:
        """**파서는 화면과 같은 것을 태스크로 본다.**

        `- [ ]` 만 있고 글자가 없으면 렌더러도 체크박스를 그리지 않는다(그냥
        글자로 나온다). 그런데도 파서가 태스크로 세면 "체크할 수 있는 것" 과
        "태스크로 세는 것" 이 갈라진다 — 집계에는 뜨는데 문서에는 누를 것이
        없는 항목이 생기고, 그 항목은 영원히 미완료로 남는다.
        """
        assert parse_tasks("- [ ]\n") == []
        # 글자가 한 자라도 있으면 태스크다.
        assert len(parse_tasks("- [ ] x\n")) == 1

    def test_it_stops_at_the_limit(self) -> None:
        body = "".join(f"- [ ] 일 {n}\n" for n in range(MAX_TASKS + 50))
        assert len(parse_tasks(body)) == MAX_TASKS

    def test_a_body_without_brackets_is_cheap_and_empty(self) -> None:
        assert parse_tasks("괄호가 아예 없는 글") == []


class TestTheLineIsTheIdentity:
    def test_the_line_points_at_the_source(self) -> None:
        body = "# 제목\n\n설명 한 줄.\n\n- [ ] 첫 일\n- [ ] 둘째 일\n"
        tasks = parse_tasks(body)
        lines = body.split("\n")
        for task in tasks:
            assert task.text in lines[task.line]

    def test_two_identical_lines_get_different_identities(self) -> None:
        """**글자가 같아도 다른 태스크다.**

        글자로 찾으면 같은 문구가 두 줄 있는 체크리스트에서 항상 첫 줄만
        체크된다 — 두 번째를 누른 사람은 화면이 안 바뀌는 것만 본다.
        """
        body = "- [ ] 검토\n- [ ] 검토\n"
        tasks = parse_tasks(body)
        assert [t.line for t in tasks] == [0, 1]

        after = set_done(body, 1, True)
        assert after == "- [ ] 검토\n- [x] 검토\n"


class TestAssigneeAndDue:
    def test_the_first_mention_is_the_assignee(self) -> None:
        body = f"- [ ] 배포 문서 정리 {_mention(ALICE)}\n"
        assert parse_tasks(body)[0].assignee == ALICE

    def test_a_second_mention_does_not_take_over(self) -> None:
        # 담당자는 하나다. 여럿이면 누가 할지 아무도 모른다.
        body = f"- [ ] 같이 볼 일 {_mention(ALICE)} {_mention(BOB, 'Bob')}\n"
        assert parse_tasks(body)[0].assignee == ALICE

    def test_no_mention_means_nobody(self) -> None:
        assert parse_tasks("- [ ] 아무나\n")[0].assignee is None

    def test_it_reads_the_due_date(self) -> None:
        body = "- [ ] 배포 due:2026-09-30\n"
        task = parse_tasks(body)[0]
        assert task.due == date(2026, 9, 30)
        # 기한 표기는 글자에서 뺀다 — 옆 칸에 날짜가 따로 있으므로 군더더기다.
        assert task.text == "배포"

    def test_a_date_without_the_marker_is_just_text(self) -> None:
        """`due:` 를 붙이는 이유. 날짜만 두면 본문의 다른 날짜와 구별할 수 없고,
        회의록에 적힌 날짜가 전부 기한이 된다."""
        task = parse_tasks("- [ ] 2026-09-30 회의 정리\n")[0]
        assert task.due is None
        assert task.text == "2026-09-30 회의 정리"

    def test_an_impossible_date_stays_as_text(self) -> None:
        """**조용히 버리지 않는다.** 버리면 사람은 기한을 적었다고 믿고
        아무 일도 안 일어난다 — 화면에 글자로 남아 있으면 오타가 보인다."""
        task = parse_tasks("- [ ] 오타 due:2026-13-40\n")[0]
        assert task.due is None
        assert "due:2026-13-40" in task.text

    def test_the_mention_stays_in_the_text(self) -> None:
        # 집계 화면이 그대로 렌더해서 담당자를 링크로 보여 줄 수 있게 남긴다.
        body = f"- [ ] 일 {_mention(ALICE)}\n"
        assert f"user:{ALICE}" in parse_tasks(body)[0].text

    def test_a_word_ending_in_due_is_not_a_due_date(self) -> None:
        task = parse_tasks("- [ ] overdue:2026-09-30 이라고 적으면\n")[0]
        assert task.due is None


class TestChecking:
    def test_it_changes_only_the_marker(self) -> None:
        """**두 글자만 바뀐다.**

        다시 직렬화하면 사람이 쓴 공백·불릿 종류·줄 끝이 조용히 정규화되고,
        판 사이 diff 가 "무엇이 바뀌었나" 를 가린다.
        """
        body = "# 제목\n\n*  [ ]   공백이 이상한 줄   \n- [ ] 보통 줄\n"
        after = set_done(body, 2, True)
        assert after == "# 제목\n\n*  [x]   공백이 이상한 줄   \n- [ ] 보통 줄\n"

    def test_unchecking_works_too(self) -> None:
        assert set_done("- [x] 되돌린다\n", 0, False) == "- [ ] 되돌린다\n"

    def test_checking_twice_is_the_same_as_once(self) -> None:
        once = set_done("- [ ] 한 번\n", 0, True)
        assert set_done(once, 0, True) == once

    def test_it_keeps_the_indentation(self) -> None:
        body = "- [ ] 큰 일\n    - [ ] 깊이 들여쓴 일\n"
        assert set_done(body, 1, True) == "- [ ] 큰 일\n    - [x] 깊이 들여쓴 일\n"

    def test_a_line_that_is_not_a_task_is_refused(self) -> None:
        # 조용히 아무 일도 안 하면 화면은 체크된 것처럼 보이고 다음 새로고침에
        # 되돌아간다. 그때는 왜인지 알 수 없다.
        with pytest.raises(ValueError):
            set_done("그냥 글\n", 0, True)

    def test_a_line_outside_the_body_is_refused(self) -> None:
        with pytest.raises(ValueError):
            set_done("- [ ] 하나뿐\n", 99, True)

    def test_the_rest_of_the_document_is_untouched(self) -> None:
        body = "앞\n\n- [ ] 하나\n- [ ] 둘\n\n뒤\n"
        after = set_done(body, 2, True)
        assert after.split("\n")[3] == "- [ ] 둘"
        assert after.split("\n")[5] == "뒤"
