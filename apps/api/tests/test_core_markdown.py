"""마크다운 파이프라인. 정본은 마크다운 텍스트다 (ADR-0008)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.markdown import dialect, excerpt, normalize, to_html, to_plaintext

SPEC_PATH = Path(__file__).resolve().parents[3] / "packages" / "markdown" / "dialect.json"


class TestDialectContract:
    def test_matches_the_shared_spec(self) -> None:
        """서버 설정과 명세 파일이 어긋나면 클라이언트와 방언이 갈라진다.

        런타임에 이 파일을 읽지 않는 이유는 배포 산출물에 저장소 레이아웃이
        따라가지 않기 때문이다. 대신 여기서 같은지 확인한다.
        """
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        spec.pop("$comment", None)
        assert dialect.as_spec() == spec

    def test_raw_html_stays_off(self) -> None:
        """이 옵션 하나가 유일한 XSS 방어선이다."""
        assert dialect.OPTIONS["html"] is False


class TestSanitizing:
    @pytest.mark.parametrize(
        "source",
        [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<iframe src='https://evil.example'></iframe>",
            "<a href='#' onclick='alert(1)'>x</a>",
            "<svg/onload=alert(1)>",
        ],
    )
    def test_raw_html_is_escaped_not_rendered(self, source: str) -> None:
        html = to_html(source)
        assert "<script" not in html.lower()
        assert "onerror" not in html.lower() or "&lt;" in html
        assert "<iframe" not in html.lower()
        assert "onload=" not in html.lower() or "&lt;" in html

    @pytest.mark.parametrize(
        "source",
        [
            "[x](javascript:alert(1))",
            "[x](JaVaScRiPt:alert(1))",
            "[x](data:text/html;base64,PHNjcmlwdD4=)",
            "[x](vbscript:msgbox)",
            "![x](javascript:alert(1))",
        ],
    )
    def test_dangerous_link_schemes_never_become_links(self, source: str) -> None:
        """원문이 본문에 남는 건 괜찮다 — 클릭 가능한 링크가 되면 안 된다."""
        html = to_html(source)
        hrefs = re.findall(r'(?:href|src)="([^"]*)"', html, flags=re.IGNORECASE)
        assert hrefs == [], hrefs

    @pytest.mark.parametrize(
        "url",
        ["https://example.com", "http://example.com", "mailto:a@b.com", "attachment:abc/x.png"],
    )
    def test_allowed_schemes_render(self, url: str) -> None:
        assert f'href="{url}"' in to_html(f"[x]({url})")

    def test_link_whitelist_is_the_policy(self) -> None:
        # 상위 라이브러리 기본값이 바뀌어도 우리 정책은 그대로여야 한다.
        assert dialect.validate_link("javascript:alert(1)") is False
        assert dialect.validate_link("issue:IEUM-1") is True
        assert dialect.validate_link("ftp://example.com") is False


class TestNormalize:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Title\n=====\n", "# Title\n"),
            ("* a\n* b\n", "- a\n- b\n"),
            ("text   \n", "text\n"),
            ("|a|b|\n|-|-|\n|1|2|\n", "| a | b |\n| --- | --- |\n| 1 | 2 |\n"),
        ],
    )
    def test_normalizes_style(self, source: str, expected: str) -> None:
        result = normalize(source)
        # 표 정렬 패딩은 mdformat 이 정하므로 공백을 접어 비교한다.
        assert " ".join(result.split()) == " ".join(expected.split())

    @pytest.mark.parametrize(
        "source",
        [
            "# heading\n\ntext\n",
            "- [ ] todo\n- [x] done\n",
            "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
            "~~strike~~ and `code`\n",
            "```python\nx = 1\n```\n",
            "---\ntitle: t\n---\n\nbody\n",
            "text[^1]\n\n[^1]: note\n",
            "> quote\n>\n> more\n",
            "1. one\n1. two\n",
            "nested\n\n- a\n  - b\n    - c\n",
        ],
    )
    def test_is_idempotent(self, source: str) -> None:
        """멱등성이 계약이다. 깨지면 저장할 때마다 diff 가 생긴다."""
        once = normalize(source)
        assert normalize(once) == once

    def test_empty_stays_empty(self) -> None:
        assert normalize("") == ""
        assert normalize("   \n\n  ") == ""

    def test_rejects_oversized_input(self) -> None:
        with pytest.raises(ValidationError, match="이하여야"):
            normalize("x" * 200_001)


class TestPlaintext:
    def test_drops_markup_but_keeps_words(self) -> None:
        plain = to_plaintext("# Title\n\nSome **bold** and [link](https://x.example).\n")
        assert plain == "Title Some bold and link."

    def test_skips_code_blocks(self) -> None:
        """코드가 색인에 섞이면 검색 결과가 코드로 뒤덮인다 (10절)."""
        plain = to_plaintext("text\n\n```python\nimport secrets\n```\n\nmore\n")
        assert "import" not in plain
        assert "text" in plain and "more" in plain

    def test_separates_table_cells(self) -> None:
        plain = to_plaintext("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
        assert "1 2" in plain
        assert "12" not in plain

    def test_drops_front_matter(self) -> None:
        assert "title" not in to_plaintext("---\ntitle: secret\n---\n\nbody\n")

    def test_excerpt_truncates_with_ellipsis(self) -> None:
        assert excerpt("word " * 100, limit=20).endswith("…")
        assert len(excerpt("word " * 100, limit=20)) <= 20
        assert excerpt("short") == "short"


class TestMentions:
    """멘션은 `[@Name](user:<uuid>)` 로 저장한다 — 이름이 바뀌어도 안 깨진다."""

    ALICE = "01a07619-e13a-751c-96f4-079a061f393f"
    BOB = "01a07619-e13a-751c-96f4-079a061f393e"

    def test_extracts_in_order_without_duplicates(self) -> None:
        from uuid import UUID

        from ieum.core.markdown import extract_mentions

        found = extract_mentions(
            f"hi [@Bob](user:{self.BOB}) and [@Alice](user:{self.ALICE}) "
            f"and [@Bob again](user:{self.BOB})"
        )
        assert found == [UUID(self.BOB), UUID(self.ALICE)]

    def test_ignores_code_blocks(self) -> None:
        """문서에 예시를 적었을 뿐인데 알림이 나가면 안 된다."""
        from ieum.core.markdown import extract_mentions

        assert extract_mentions(f"```\n[@Alice](user:{self.ALICE})\n```") == []
        assert extract_mentions(f"`[@Alice](user:{self.ALICE})`") == []

    def test_ignores_non_uuid_targets(self) -> None:
        from ieum.core.markdown import extract_mentions

        assert extract_mentions("[@bob](user:bob)") == []

    def test_caps_the_count(self) -> None:
        """수신자마다 권한 검사가 돈다. 상한이 없으면 코멘트 하나가 DB 를 잡는다."""
        from ieum.core.ids import new_id
        from ieum.core.markdown import MAX_MENTIONS, extract_mentions

        body = " ".join(f"[@u](user:{new_id()})" for _ in range(MAX_MENTIONS + 10))
        assert len(extract_mentions(body)) == MAX_MENTIONS

    def test_survives_normalization(self) -> None:
        """정규화가 멘션 링크를 망가뜨리면 저장하는 순간 사라진다."""
        from ieum.core.markdown import extract_mentions, normalize

        body = f"cc [@Alice](user:{self.ALICE})"
        assert extract_mentions(normalize(body)) == extract_mentions(body)

    def test_user_scheme_renders_as_a_link_server_side(self) -> None:
        """서버 렌더는 메일용이다. 칩으로 바꾸는 건 클라이언트가 한다."""
        from ieum.core.markdown import to_html

        assert f'href="user:{self.ALICE}"' in to_html(f"[@Alice](user:{self.ALICE})")
