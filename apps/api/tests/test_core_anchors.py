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
    relocate_all,
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


def _long_document(chars: int) -> str:
    """앵커 하나를 찾는 데 실제로 오래 걸리는 크기의 평문.

    같은 문장을 반복하면 안 된다 — `difflib` 이 씨앗을 바로 찾아서 싸게
    끝나 버린다. 실제 문서처럼 **같은 단어가 흩어져 있는** 글을 만든다.
    """
    words = [f"낱말{i:03d}" for i in range(400)]
    out: list[str] = []
    seed, so_far = 7, 0
    while so_far < chars:
        seed = (seed * 1103515245 + 12345) % (2**31)
        word = words[seed % len(words)]
        out.append(word)
        so_far += len(word) + 1
    return " ".join(out)


class TestFuzzyPruningKeepsTheAnswer:
    """창 점수를 `quick_ratio` 상한으로 거른다 — **값이 바뀌면 안 된다.**

    거르는 것은 일을 줄이려는 것이지 답을 바꾸려는 것이 아니다. 기준을 잘못
    잡으면(예: 이미 찾은 점수 대신 고정값으로 자르면) 더 잘 맞는 창을 조용히
    버리고, 코멘트가 **한 칸 밀린 자리**에 붙는다. 그건 고아보다 나쁘다 —
    틀렸는데 틀린 줄을 모른다.

    그래서 여기서는 가지치기 없이 창을 전부 재는 계산을 따로 적고, 둘이
    같은 답을 내는지 본다.
    """

    @staticmethod
    def _best_window_without_pruning(text: str, quote: str) -> tuple[int, int] | None:
        from difflib import SequenceMatcher

        from ieum.core.markdown.anchors import _SHIFTS, _STRETCHES

        # 가지치기 **한 줄만** 빼고 나머지는 본문과 똑같이 둔다. 비교 대상이
        # 다른 데서도 달라지면, 아래 속도 테스트가 무엇 덕에 빨라졌는지를
        # 못 가른다 — 실제로 한 번 그렇게 속았다(맷처를 새로 만드느라 느린
        # 것을 가지치기 덕이라고 읽었다).
        text, quote = normalize_text(text), normalize_text(quote)
        matcher = SequenceMatcher(None, "", quote, autojunk=False)
        matcher.set_seq1(text)
        seed = matcher.find_longest_match(0, len(text), 0, len(quote))
        if seed.size == 0:
            return None
        best: tuple[float, int, int] | None = None
        origin = seed.a - seed.b
        for shift in _SHIFTS:
            start = max(0, min(len(text), origin + shift))
            for stretch in _STRETCHES:
                end = min(len(text), start + len(quote) + stretch)
                if end <= start:
                    continue
                matcher.set_seq1(text[start:end])
                score = matcher.ratio()
                if score >= FUZZY_THRESHOLD and (best is None or score > best[0]):
                    best = (score, start, end)
        return None if best is None else (best[1], best[2])

    @pytest.mark.parametrize(
        ("body", "quote"),
        [
            # 글자 하나가 바뀌었다 — 가장 잘 맞는 창이 정확히 한 곳이다.
            ("배포 전 마이그레이션을 검토한다. 롤백 계획을 준비한다.", "마이그레이션을 겁토한다"),
            # 앞에 글이 붙어 자리가 밀렸다.
            ("머리말이 붙었다. " * 4 + BODY, "마이그레이션을 검토한다"),
            # 인용 가운데가 늘어났다 — 창을 늘여 봐야 맞는다.
            ("배포 전 마이그레이션을 아주 꼼꼼히 검토한다.", "마이그레이션을 검토한다"),
            # 아예 다른 글 — 둘 다 고아라고 해야 한다.
            ("점심은 김치찌개로 하자.", "마이그레이션을 검토한다"),
        ],
    )
    def test_pruned_and_unpruned_agree(self, body: str, quote: str) -> None:
        match = locate(body, Anchor(exact=quote))
        expected = self._best_window_without_pruning(body, quote)
        if expected is None:
            assert match is None
            return
        assert match is not None
        assert (match.start, match.end) == expected

    def test_it_really_is_faster(self) -> None:
        """가지치기를 빼면 느려진다 — 이 테스트가 지키는 것이 그 차이다.

        **고아**로 잰다. 인용이 그대로 있으면 정확 일치에서 끝나 퍼지를 아예
        안 타고, 그러면 이 테스트는 아무것도 안 지킨다(한 번 그렇게 썼다가
        가지치기를 통째로 빼도 초록인 것을 보고 알았다).
        """
        import time

        body = _long_document(20_000)
        quote = "없는말000 " * 100

        started = time.perf_counter()
        assert locate(body, Anchor(exact=quote)) is None
        pruned = time.perf_counter() - started

        started = time.perf_counter()
        assert self._best_window_without_pruning(body, quote) is None
        plain = time.perf_counter() - started

        # 잴 만큼은 걸려야 비교가 뜻이 있다. 문서가 작아지면 둘 다 0 이 되고
        # 배수는 잡음이 된다.
        assert plain > 0.05, f"전부 재기가 {plain:.3f}s 밖에 안 걸렸다 — 비교가 안 된다"
        assert pruned < plain / 2, f"가지치기 {pruned:.3f}s / 전부 재기 {plain:.3f}s"


class TestRelocateAll:
    """코멘트 목록 한 번에 앵커를 다시 붙인다.

    하나씩 `locate` 를 부르면 고아가 몇 개만 있어도 한 요청이 수십 초가 된다.
    그동안 **이 워커가 받아 둔 다른 모든 요청이 같이 선다.**
    """

    def test_it_matches_locate_one_by_one(self) -> None:
        raws = [
            Anchor(exact="마이그레이션을 검토한다").as_json(),  # 그대로 있다
            None,  # 자리를 안 가리키는 코멘트
            Anchor(exact="마이그레이션을 겁토한다").as_json(),  # 퍼지
            Anchor(exact="점심은 김치찌개로").as_json(),  # 고아
            Anchor(exact="").as_json(),  # 빈 인용
        ]
        got = relocate_all(BODY, raws)
        assert [r.decided for r in got] == [True] * 5

        for raw, one in zip(raws, got, strict=True):
            expected = locate(BODY, Anchor.from_json(raw)) if raw else None
            assert (expected is None) == (one.match is None)
            if expected is not None and one.match is not None:
                assert (expected.start, expected.end, expected.how) == (
                    one.match.start,
                    one.match.end,
                    one.match.how,
                )

    def test_a_page_full_of_orphans_only_searches_a_few(self) -> None:
        """고아 40건이어도 퍼지로 넘어가는 것은 **몇 건뿐**이다.

        여기서 초를 재면 안 된다. 처음에 `3초 미만`으로 썼다가 CI 러너에서
        7.6초가 나와 붉었다 — 기계가 느린 것이지 코드가 달라진 게 아니다.
        그런 단언은 영원히 기계 성능을 쫓아다니게 되고, 결국 문턱을 올리다가
        아무것도 안 지키는 값이 된다.

        지킬 것은 "빠른가" 가 아니라 **"예산이 실제로 막는가"** 이고, 그건
        몇 건을 찾아봤는지로 그대로 셀 수 있다. 12만 자 문서에 기본 예산
        (30만 자)이면 두 건 남짓이다. 마흔 건을 다 돌았다면 예산이 없는 것이다.

        한 건의 비용이 커지는 쪽은 `test_it_really_is_faster` 가 본다 —
        거기는 절대 시간이 아니라 **배수**라 기계를 안 탄다.
        """
        body = _long_document(120_000)
        orphans = [Anchor(exact=f"없는말{i:03d} " * 120).as_json() for i in range(40)]

        got = relocate_all(body, orphans)
        assert len(got) == 40

        searched = sum(1 for one in got if one.decided)
        assert 1 <= searched <= 5, searched
        # 나머지는 "못 찾았다" 가 아니라 "안 찾아봤다" 로 남아야 한다.
        assert all(one.match is None for one in got)

    def test_what_it_did_not_search_is_not_called_an_orphan(self) -> None:
        """예산이 떨어지면 **모른다**고 해야 한다.

        여기서 `decided=True` 를 주면 부르는 쪽이 "찾아봤는데 없더라" 로 읽고
        고아 표시를 단다 — 본문에 멀쩡히 살아 있는 코멘트에.
        """
        body = _long_document(60_000)
        orphans = [Anchor(exact=f"없는말{i:03d} " * 120).as_json() for i in range(30)]

        got = relocate_all(body, orphans, budget_chars=1)
        assert got[0].decided, "첫 건은 예산을 넘겨도 해 본다"
        assert [r.decided for r in got[1:]] == [False] * 29
        assert all(r.match is None for r in got)

    def test_the_budget_only_gates_the_expensive_half(self) -> None:
        """예산이 0이어도 **그대로 있는** 인용은 전부 찾는다.

        정확 일치는 문자열 탐색이라 거의 공짜다. 이걸 같이 막으면 고아 몇
        건 때문에 멀쩡한 코멘트 전부가 하이라이트를 잃는다.
        """
        alive = Anchor(exact="마이그레이션을 검토한다").as_json()
        orphan = Anchor(exact="없는말 " * 200).as_json()
        got = relocate_all(BODY, [orphan, alive, orphan, alive], budget_chars=1)

        assert [r.decided for r in got] == [True, True, False, True]
        assert [r.match is not None for r in got] == [False, True, False, True]
