"""자리 어휘와 값 검사 (M6 "플러그인 훅"). **DB 를 쓰지 않는다.**

여기서 붙잡는 것이 이 기능의 보안 경계다:

- **`javascript:` 는 통과하지 못한다.** 등록한 주소는 우리 화면의 앵커가
  되므로, 하나 통과하면 그 이슈를 여는 사람 전부가 누를 수 있는 자리에 놓인다.
- **모르는 자리는 거절한다.** 조용히 아무 데도 안 나오는 설정이 제일 나쁘다.
- **자리와 종류가 맞아야 한다.** 링크 자리에 패널을 놓으면 화면은 아무것도
  안 그리고, 등록한 사람은 이유를 알 수 없다.
"""

from __future__ import annotations

import pytest

from ieum.core.exceptions import ValidationError
from ieum.modules.plugins import slots


class TestSlotVocabulary:
    def test_a_known_slot_comes_back(self) -> None:
        assert slots.slot("issue.panel").kind == "panel"

    def test_an_unknown_slot_is_refused(self) -> None:
        """앱이 자리 이름을 지어 낼 수 없다."""
        with pytest.raises(ValidationError) as caught:
            slots.slot("issue.sidebar.top")
        assert caught.value.code == "plugins.unknown_slot"
        # 무엇을 쓸 수 있는지 함께 말한다 — 거절만 하면 고칠 수가 없다.
        assert "known" in (caught.value.details or {})

    def test_the_catalog_says_what_each_slot_takes(self) -> None:
        """화면이 자리 이름을 자기가 들고 있으면 서버가 늘렸을 때 어긋난다."""
        found = {row["name"]: row for row in slots.catalog()}
        assert found["issue.link"]["kind"] == "link"
        assert "issue_key" in found["issue.link"]["placeholders"]
        # 설정 목록은 이슈를 모른다. 자리표를 주면 빈 값이 주소에 실린다.
        assert found["settings.link"]["placeholders"] == []


class TestKind:
    def test_a_matching_kind_passes(self) -> None:
        assert slots.check_kind(slots.slot("issue.link"), "link") == "link"

    def test_a_panel_cannot_take_a_link_slot(self) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.check_kind(slots.slot("issue.link"), "panel")
        assert caught.value.code == "plugins.kind_mismatch"

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.check_kind(slots.slot("issue.link"), "iframe")
        assert caught.value.code == "plugins.unknown_kind"


class TestUrlTemplate:
    @pytest.mark.parametrize(
        "raw",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox",
            "file:///etc/passwd",
            "http://example.com/build",
        ],
    )
    def test_only_https_gets_through(self, raw: str) -> None:
        """**이 시험이 이 함수가 있는 이유다.**

        `http` 까지 막는 것은 취향이 아니다 — 앱 링크는 사람이 눌러 바깥으로
        나가는 자리이고, 평문으로 나가는 주소를 우리 화면이 권할 이유가 없다.
        """
        with pytest.raises(ValidationError) as caught:
            slots.clean_url_template(slots.slot("issue.link"), raw)
        assert caught.value.code == "plugins.url_scheme"

    def test_https_with_a_host_passes(self) -> None:
        found = slots.clean_url_template(
            slots.slot("issue.link"), "https://ci.example.com/b/{issue_key}"
        )
        assert found == "https://ci.example.com/b/{issue_key}"

    @pytest.mark.parametrize("raw", ["https:oops", "https://", "https:// "])
    def test_https_without_a_host_is_refused(self, raw: str) -> None:
        """스킴만 맞고 갈 곳이 없는 주소. 스킴 검사와 **따로** 걸린다 —
        둘을 한 오류로 뭉치면 적은 사람이 무엇이 틀렸는지 알 수 없다."""
        with pytest.raises(ValidationError) as caught:
            slots.clean_url_template(slots.slot("issue.link"), raw)
        assert caught.value.code == "plugins.url_no_host"

    def test_an_unknown_placeholder_is_refused(self) -> None:
        """`{issueId}` 로 적은 사람의 링크에 중괄호가 글자로 실려 나가면
        누른 사람이 깨진 주소를 본다. 등록할 때 잡는다."""
        with pytest.raises(ValidationError) as caught:
            slots.clean_url_template(slots.slot("issue.link"), "https://ci.example.com/{issueId}")
        assert caught.value.code == "plugins.unknown_placeholder"
        assert (caught.value.details or {})["unknown"] == ["issueId"]

    def test_a_slot_that_knows_no_issue_refuses_issue_placeholders(self) -> None:
        """설정 목록의 링크에 `{issue_key}` 를 두면 채울 값이 없다."""
        with pytest.raises(ValidationError) as caught:
            slots.clean_url_template(
                slots.slot("settings.link"), "https://x.example.com/{issue_key}"
            )
        assert caught.value.code == "plugins.unknown_placeholder"

    def test_an_empty_url_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.clean_url_template(slots.slot("issue.link"), "   ")
        assert caught.value.code == "plugins.url_required"


class TestFill:
    def test_it_fills_what_it_knows(self) -> None:
        found = slots.fill(
            "https://ci.example.com/{project_key}/{issue_key}",
            {"project_key": "ENG", "issue_key": "ENG-12"},
        )
        assert found == "https://ci.example.com/ENG/ENG-12"

    def test_it_does_not_reach_into_objects(self) -> None:
        """`str.format` 을 쓰지 않는 이유. 틀은 밖에서 온 글이다 —
        `format` 이면 `{0.__class__}` 같은 접근이 열린다."""
        found = slots.fill("https://x.example.com/{0.__class__}", {})
        # 아는 자리표가 아니므로 그대로 남는다. 예외도, 객체 접근도 없다.
        assert found == "https://x.example.com/{0.__class__}"


class TestSlug:
    @pytest.mark.parametrize("raw", ["CI-Bot", "  ci-bot  ", "ci-bot"])
    def test_it_normalises(self, raw: str) -> None:
        assert slots.clean_slug(raw) == "ci-bot"

    @pytest.mark.parametrize("raw", ["x", "-bot", "ci bot", "ci_bot", "봇", "a" * 65, ""])
    def test_it_refuses_what_does_not_belong_in_a_url(self, raw: str) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.clean_slug(raw)
        assert caught.value.code == "plugins.invalid_slug"


class TestLabel:
    def test_it_trims(self) -> None:
        assert slots.clean_label("  빌드  ") == "빌드"

    def test_an_empty_label_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.clean_label("   ")
        assert caught.value.code == "plugins.label_required"

    def test_a_long_label_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            slots.clean_label("가" * 61)
        assert caught.value.code == "plugins.label_too_long"
