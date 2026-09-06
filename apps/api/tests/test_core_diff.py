"""판 사이 라인 diff (wiki-markdown.md 9절).

렌더 결과가 아니라 마크다운 원문을 비교한다. 본문이 이미 정규화돼 있어
서식 흔들림이 섞이지 않는 것이 이 기능의 전제다 — 정규화가 없었으면 한
글자만 고쳐도 문서 절반이 바뀐 것으로 보였다.
"""

from __future__ import annotations

from ieum.core.markdown import normalize
from ieum.core.markdown.diff import CONTEXT_LINES, MAX_LINES, diff_lines


class TestDiffLines:
    def test_finds_a_changed_line(self) -> None:
        result = diff_lines("a\nb\nc", "a\nB\nc")
        assert (result.added, result.removed) == (1, 1)
        assert [(line.op, line.text) for line in result.lines if line.op != "equal"] == [
            ("delete", "b"),
            ("insert", "B"),
        ]

    def test_line_numbers_point_at_both_sides(self) -> None:
        result = diff_lines("a\nb", "a\nb\nc")
        added = next(line for line in result.lines if line.op == "insert")
        assert (added.old_number, added.new_number) == (None, 3)

    def test_identical_bodies_have_no_changes(self) -> None:
        result = diff_lines("같다\n그대로", "같다\n그대로")
        assert (result.added, result.removed) == (0, 0)
        assert all(line.op == "equal" for line in result.lines)

    def test_an_empty_before_is_all_additions(self) -> None:
        result = diff_lines("", "새 문서\n두 줄")
        assert result.removed == 0
        assert result.added == 2

    def test_an_emptied_body_is_all_deletions(self) -> None:
        result = diff_lines("지워진다", "")
        assert (result.added, result.removed) == (0, 1)

    def test_far_apart_context_is_dropped(self) -> None:
        """통짜 문서를 다 보여 주면 무엇이 바뀌었는지 오히려 안 보인다."""
        body = "\n".join(f"line {n}" for n in range(50))
        changed = body.replace("line 25", "line 25 고침")
        result = diff_lines(body, changed)
        # 바뀐 자리 둘레만 남는다.
        assert len(result.lines) < 50
        assert any("25" in line.text for line in result.lines)

    def test_context_size_is_respected(self) -> None:
        body = "\n".join(f"L{n}" for n in range(30))
        result = diff_lines(body, body.replace("L15", "L15!"), context=1)
        equals = [line for line in result.lines if line.op == "equal"]
        # 앞 구간 1줄 + 뒤 구간 1줄씩, 양쪽 끝 각각 1줄.
        assert len(equals) <= 6

    def test_a_table_change_is_one_line(self) -> None:
        """표에 라인 diff 가 잘 맞는 것이 마크다운을 고른 부수 이득이다."""
        before = normalize("| a | b |\n| --- | --- |\n| 1 | 2 |")
        after = normalize("| a | b |\n| --- | --- |\n| 1 | 3 |")
        result = diff_lines(before, after)
        assert (result.added, result.removed) == (1, 1)

    def test_reformatting_does_not_show_up_after_normalize(self) -> None:
        # 정규화가 목록 마커를 통일한다. 그 차이가 diff 에 새면 안 된다.
        before = normalize("* 하나\n* 둘")
        after = normalize("-   하나\n-   둘")
        assert diff_lines(before, after).added == 0

    def test_a_huge_document_is_truncated(self) -> None:
        big = "\n".join(str(n) for n in range(MAX_LINES + 100))
        result = diff_lines(big, big + "\n끝")
        assert result.truncated

    def test_the_default_context_is_used(self) -> None:
        body = "\n".join(f"L{n}" for n in range(20))
        result = diff_lines(body, body.replace("L10", "L10!"))
        around = [
            line for line in result.lines if line.op == "equal" and line.old_number in {9, 10}
        ]
        assert len(around) == 2
        assert CONTEXT_LINES >= 1
