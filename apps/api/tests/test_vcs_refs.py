"""커밋 메시지에서 이슈 찾기 (A22). **DB 를 세우지 않는다.**

사람이 손으로 쓴 글에서 뜻을 읽는 일이라, 틀리는 방향이 둘이고 둘 다 비싸다:

- **못 찾으면** 링크가 안 생기고, 사람은 연동이 안 된다고 여긴다.
- **잘못 찾으면** 남의 이슈에 남의 커밋이 붙는다.

그래서 값을 손으로 적어 붙잡는다.
"""

from __future__ import annotations

import pytest

from ieum.modules.vcs.refs import find_refs

PROJECTS = ("ENG", "OPS", "A1")


def keys(text: str, *, projects: tuple[str, ...] = PROJECTS) -> list[str]:
    return sorted(ref.key for ref in find_refs(text, known_projects=projects))


class TestFindingKeys:
    def test_a_plain_key(self) -> None:
        assert keys("ENG-12 를 고쳤다") == ["ENG-12"]

    @pytest.mark.parametrize(
        "text",
        [
            "(ENG-12) 괄호 안",
            "[ENG-12] 대괄호",
            "ENG-12. 문장 끝",
            "ENG-12, 쉼표",
            "제목: ENG-12",
            "feature/ENG-12-something 브랜치",
            "여러 줄\n\nENG-12\n",
        ],
    )
    def test_people_put_keys_inside_sentences(self, text: str) -> None:
        """**사람이 쓰는 모양을 다 받는다.** 하나만 놓쳐도 그 습관을 가진
        사람에게는 연동이 안 되는 것으로 보인다."""
        assert keys(text) == ["ENG-12"]

    def test_it_does_not_cut_a_longer_number(self) -> None:
        """**이 시험이 제일 비싼 오검출을 막는다.**

        `ENG-121` 에서 `ENG-12` 를 잘라 오면, 지운 이슈 자리에 남의 커밋이
        붙는다 — 그리고 그건 목록을 봐도 틀린 줄 모른다.
        """
        assert keys("ENG-121 을 고쳤다") == ["ENG-121"]
        assert keys("ENG-12 와 ENG-121") == ["ENG-12", "ENG-121"]

    @pytest.mark.parametrize("text", ["xENG-12", "12-ENG-12", "SOME-ENG-12", "eng-12"])
    def test_it_needs_a_real_boundary(self, text: str) -> None:
        """앞이 글자·숫자·하이픈이면 그 키가 아니다. 소문자도 키가 아니다 —
        프로젝트 키는 대문자다(`org` 의 `key = upper(key)` 제약)."""
        assert keys(text) == []

    def test_it_drops_unknown_projects(self) -> None:
        """**모양만으로는 가릴 수 없다.** `UTF-8` 도 대문자다.

        판정은 프로젝트 키 목록이 한다. 모양만 보고 링크하면 커밋마다 유령
        이슈가 붙고, 사람은 곧 그 목록을 안 본다.
        """
        assert keys("UTF-8 로 저장하고 SHA-1 을 쓴다. RFC-2119 참고") == []
        assert keys("ENG-12 와 UTF-8") == ["ENG-12"]

    def test_a_number_only_project_key_is_allowed(self) -> None:
        """`A1` 같은 키도 있다. 첫 글자만 영문이면 된다."""
        assert keys("A1-3 를 본다") == ["A1-3"]

    def test_the_same_key_twice_is_one_ref(self) -> None:
        """제목과 본문에 같은 키를 쓰는 것이 흔하다. 링크를 둘 만들지 않는다."""
        assert keys("ENG-12 고침\n\n자세히는 ENG-12 참고") == ["ENG-12"]

    def test_leading_zeros_are_the_same_issue(self) -> None:
        """`ENG-007` 과 `ENG-7` 은 같은 이슈다. 숫자로 읽는다."""
        assert keys("ENG-007") == ["ENG-7"]

    def test_a_long_number_is_not_a_key(self) -> None:
        # 열 자리는 이슈 번호가 아니다. 전화번호·해시 조각이 그렇게 생긴다.
        assert keys("ENG-1234567890") == []


class TestClosingWords:
    def _closing(self, text: str) -> dict[str, bool]:
        return {ref.key: ref.closing for ref in find_refs(text, known_projects=PROJECTS)}

    @pytest.mark.parametrize(
        "word", ["fixes", "Fixes", "FIXES", "fix", "fixed", "closes", "close", "resolves"]
    )
    def test_it_reads_the_usual_words(self, word: str) -> None:
        """GitHub·GitLab 이 쓰는 낱말을 그대로 받는다 — 사람이 이미 그 습관을
        갖고 있다."""
        assert self._closing(f"{word} ENG-12") == {"ENG-12": True}

    @pytest.mark.parametrize("text", ["ENG-12 참고", "touches ENG-12", "see ENG-12"])
    def test_a_plain_mention_is_not_closing(self, text: str) -> None:
        assert self._closing(text) == {"ENG-12": False}

    def test_a_word_two_clauses_away_does_not_stick(self) -> None:
        """**창을 좁게 둔다.** `fixes the build, touches ENG-12` 에서 그
        `fixes` 가 붙으면 사람이 안 쓴 말을 우리가 적는 것이다."""
        assert self._closing("fixes the build, touches ENG-12") == {"ENG-12": False}

    def test_a_key_glued_to_a_word_is_not_a_key_at_all(self) -> None:
        """`fixesENG-12` 는 이슈 참조가 아니다 — 경계가 없다. 닫는지를 묻기
        전에 키로 읽히지 않아야 한다."""
        assert self._closing("fixesENG-12") == {}

    @pytest.mark.parametrize("word", ["prefixes", "postfixes", "unfixed", "affixes"])
    def test_a_word_that_merely_contains_fixes_does_not_count(self, word: str) -> None:
        """**낱말로 본다.** 부분 문자열로 보면 `prefixes ENG-12` 가 닫는
        말이 되고, 그러면 우리가 사람이 안 쓴 말을 적는 것이다."""
        assert self._closing(f"{word} ENG-12") == {"ENG-12": False}

    def test_a_colon_counts_as_a_separator(self) -> None:
        assert self._closing("fixes: ENG-12") == {"ENG-12": True}

    def test_one_closing_mention_is_enough(self) -> None:
        """제목에 `fixes`, 본문에 맨 키 — 흔한 모양이다. 닫는다고 읽는다."""
        assert self._closing("fixes ENG-12\n\n자세히는 ENG-12") == {"ENG-12": True}

    def test_each_key_gets_its_own_reading(self) -> None:
        found = self._closing("fixes ENG-12, and touches OPS-3")
        assert found == {"ENG-12": True, "OPS-3": False}
