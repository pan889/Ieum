"""본문에서 내부 링크 뽑기 (wiki-markdown.md 5절).

정규식이 아니라 파서 토큰에서 뽑는다. 정규식은 코드 블록 안의 예시도 링크로
읽어서, 문법을 설명한 문서가 실제로 그 이슈를 참조한 것이 된다.
"""

from __future__ import annotations

import pytest

from ieum.core.markdown.links import ISSUE, MAX_LINKS, PAGE, LinkRef, extract_links


class TestExtractLinks:
    def test_reads_both_schemes(self) -> None:
        found = extract_links("[a](issue:ENG-1) 와 [b](page:ENG/deploy)")
        assert found == [LinkRef(ISSUE, "ENG-1"), LinkRef(PAGE, "ENG/deploy")]

    def test_keeps_order_and_drops_duplicates(self) -> None:
        found = extract_links("[a](issue:B-2) [b](issue:A-1) [c](issue:B-2)")
        assert [r.target for r in found] == ["B-2", "A-1"]

    def test_ignores_other_schemes(self) -> None:
        assert extract_links("[x](https://example.com) [y](mailto:a@b.c)") == []

    def test_code_blocks_are_examples(self) -> None:
        """문법을 설명한 문서가 그 이슈를 참조한 것이 되면 안 된다."""
        assert extract_links("```\n[a](issue:ENG-9)\n```") == []

    def test_inline_code_is_not_a_link(self) -> None:
        assert extract_links("`[a](issue:ENG-9)` 처럼 씁니다") == []

    def test_can_ask_for_one_scheme(self) -> None:
        found = extract_links("[a](issue:E-1) [b](page:S/x)", schemes=(ISSUE,))
        assert [r.scheme for r in found] == [ISSUE]

    def test_trailing_slashes_are_trimmed(self) -> None:
        assert extract_links("[a](page:ENG/deploy/)") == [LinkRef(PAGE, "ENG/deploy")]

    def test_an_empty_target_is_not_a_link(self) -> None:
        assert extract_links("[a](issue:)") == []

    def test_stops_at_the_cap(self) -> None:
        # 상한이 없으면 문서 하나가 링크 표를 수천 줄로 만든다.
        body = " ".join(f"[x](issue:E-{n})" for n in range(MAX_LINKS + 20))
        assert len(extract_links(body)) == MAX_LINKS

    @pytest.mark.parametrize("text", ["", "링크 없음", "issue:ENG-1 (그냥 글자)"])
    def test_nothing_to_find(self, text: str) -> None:
        assert extract_links(text) == []
