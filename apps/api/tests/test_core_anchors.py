"""인라인 코멘트 앵커 (wiki-markdown.md 6절).

마크다운에는 노드 id 가 없어서 인용한 텍스트로 위치를 잡는다. 문서가 고쳐지면
인용이 움직이거나 사라진다 — 그때 **조용히 지우지 않는 것**이 이 기능의 핵심
이라, "못 찾으면 None" 이 가장 중요한 계약이다.
"""

from __future__ import annotations

import pytest

from ieum.core.markdown.anchors import (
    FUZZY_THRESHOLD,
    Anchor,
    build_anchor,
    locate,
    normalize_text,
)

BODY = "배포 전 마이그레이션을 검토한다. 운영 반영 시 롤백 계획을 준비한다."


class TestExactMatch:
    def test_finds_the_quote(self) -> None:
        match = locate(BODY, Anchor(exact="마이그레이션을 검토한다"))
        assert match is not None
        assert (match.how, match.score) == ("exact", 1.0)
        assert BODY[match.start : match.end] == "마이그레이션을 검토한다"

    def test_whitespace_does_not_break_it(self) -> None:
        """줄바꿈·들여쓰기가 바뀌었다고 고아가 되면 안 된다."""
        match = locate(
            "배포 전\n  마이그레이션을    검토한다.", Anchor(exact="마이그레이션을 검토한다")
        )
        assert match is not None
        assert match.how == "exact"

    def test_missing_quote_is_an_orphan(self) -> None:
        assert locate("완전히 다른 내용입니다.", Anchor(exact="마이그레이션을 검토한다")) is None

    def test_an_empty_quote_anchors_nowhere(self) -> None:
        assert locate(BODY, Anchor(exact="   ")) is None

    def test_an_empty_document_anchors_nowhere(self) -> None:
        assert locate("", Anchor(exact="무엇이든")) is None


class TestDisambiguation:
    TEXT = "같은 문장이다. 사이에 다른 말. 같은 문장이다. 끝."

    def test_occurrence_picks_the_nth(self) -> None:
        first = locate(self.TEXT, Anchor(exact="같은 문장이다", occurrence=1))
        second = locate(self.TEXT, Anchor(exact="같은 문장이다", occurrence=2))
        assert first is not None and second is not None
        assert first.start < second.start

    def test_context_wins_over_occurrence(self) -> None:
        """앞뒤 문맥이 더 확실한 단서다. 문장이 추가돼도 순번보다 잘 버틴다."""
        match = locate(self.TEXT, Anchor(exact="같은 문장이다", prefix="사이에 다른 말. "))
        assert match is not None
        assert match.start > 10

    def test_an_out_of_range_occurrence_falls_back_to_the_first(self) -> None:
        match = locate(self.TEXT, Anchor(exact="같은 문장이다", occurrence=9))
        assert match is not None
        assert match.start == 0


class TestFuzzyMatch:
    def test_survives_a_small_edit(self) -> None:
        match = locate(
            "배포 전에 마이그레이션을 꼭 검토한다.", Anchor(exact="마이그레이션을 검토한다")
        )
        assert match is not None
        assert match.how == "fuzzy"
        assert match.score >= FUZZY_THRESHOLD
        assert "마이그레이션" in match.found

    def test_survives_text_moving_down(self) -> None:
        moved = "앞에 새 문단이 길게 붙었다. " * 5 + BODY
        match = locate(moved, Anchor(exact="마이그레이션을 검토한다"))
        assert match is not None
        assert moved[match.start : match.end].strip() == "마이그레이션을 검토한다"

    def test_a_rewritten_sentence_is_an_orphan(self) -> None:
        """비슷하기만 하면 아무 데나 붙는다. 그건 조용히 틀리는 것이다."""
        assert locate("점심은 김치찌개로 하자.", Anchor(exact="마이그레이션을 검토한다")) is None

    def test_is_fast_on_a_long_document(self) -> None:
        """모든 위치에 창을 대면 큰 문서에서 한 요청이 오래 걸린다."""
        import time

        big = "가나다라마바사 아자차카타파하. " * 8000
        start = time.perf_counter()
        locate(big, Anchor(exact="마이그레이션을 검토한다"))
        assert time.perf_counter() - start < 1.0


class TestBuildAnchor:
    def test_captures_context_around_the_selection(self) -> None:
        text = normalize_text(BODY)
        start = text.index("마이그레이션을 검토한다")
        anchor = build_anchor(text, start, start + len("마이그레이션을 검토한다"), version_number=3)
        assert anchor.exact == "마이그레이션을 검토한다"
        assert anchor.prefix.endswith("배포 전 ")
        assert anchor.suffix.startswith(".")
        assert anchor.version_number == 3

    def test_round_trips_through_locate(self) -> None:
        text = normalize_text(BODY)
        start = text.index("롤백 계획")
        anchor = build_anchor(text, start, start + 5)
        match = locate(BODY, anchor)
        assert match is not None
        assert match.how == "exact"

    @pytest.mark.parametrize(("start", "end"), [(-5, 3), (0, 9999), (10, 2)])
    def test_out_of_range_offsets_do_not_crash(self, start: int, end: int) -> None:
        build_anchor(BODY, start, end)


class TestJson:
    def test_round_trips(self) -> None:
        anchor = Anchor(exact="a", prefix="b", suffix="c", occurrence=2, version_number=7)
        assert Anchor.from_json(anchor.as_json()) == anchor

    def test_tolerates_junk_from_older_rows(self) -> None:
        # 저장된 JSON 은 우리가 쓴 것이지만, 스키마가 바뀌어도 읽혀야 한다.
        anchor = Anchor.from_json({"exact": "a", "occurrence": "많이", "version_number": "x"})
        assert (anchor.occurrence, anchor.version_number) == (1, None)
