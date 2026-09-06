"""서버측 번역기. 알림·이메일이 수신자 언어로 나가는지의 근거다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ieum.core.i18n import Translator, render, translator_for

CATALOG = Path(__file__).resolve().parents[3] / "packages" / "i18n"


class TestRender:
    def test_simple_substitution(self) -> None:
        assert render("Hello {name}", {"name": "Ieum"}) == "Hello Ieum"

    def test_missing_param_left_as_is(self) -> None:
        """빈칸으로 두면 문장이 이상해진다. 자리표시자를 남기면 원인이 보인다."""
        assert render("Hello {name}", {}) == "Hello {name}"

    def test_plural_one_and_other(self) -> None:
        template = "{count, plural, one {# issue} other {# issues}}"
        assert render(template, {"count": 1}) == "1 issue"
        assert render(template, {"count": 5}) == "5 issues"

    def test_plural_exact_match_wins(self) -> None:
        template = "{count, plural, =0 {none} one {# item} other {# items}}"
        assert render(template, {"count": 0}) == "none"

    def test_plural_without_one_falls_back_to_other(self) -> None:
        """한국어처럼 복수형이 없는 언어는 other 만 채운다."""
        assert render("{count, plural, other {#개}}", {"count": 1}) == "1개"

    def test_select(self) -> None:
        template = "{kind, select, bug {버그} other {작업}}"
        assert render(template, {"kind": "bug"}) == "버그"
        assert render(template, {"kind": "task"}) == "작업"

    def test_nested_params_inside_choice(self) -> None:
        template = "{count, plural, other {{name} has # items}}"
        assert render(template, {"count": 3, "name": "Ieum"}) == "Ieum has 3 items"


class TestTranslator:
    @pytest.mark.parametrize("locale", ["en", "ko"])
    def test_real_catalog_loads(self, locale: str) -> None:
        t = translator_for(CATALOG, locale)
        assert t.translate("common:action.save")

    def test_recipient_locale_decides(self) -> None:
        """같은 키가 언어별로 다르게 나와야 한다."""
        english = translator_for(CATALOG, "en").translate("common:action.save")
        korean = translator_for(CATALOG, "ko").translate("common:action.save")
        assert english != korean

    def test_plural_differs_between_languages(self) -> None:
        en = translator_for(CATALOG, "en")
        ko = translator_for(CATALOG, "ko")
        assert en.translate("common:pagination.itemCount", count=1) == "1 item"
        assert en.translate("common:pagination.itemCount", count=3) == "3 items"
        # 한국어는 수량에 따라 형태가 바뀌지 않는다.
        assert ko.translate("common:pagination.itemCount", count=1) == "1개"
        assert ko.translate("common:pagination.itemCount", count=3) == "3개"

    def test_unknown_locale_falls_back_to_english(self) -> None:
        t = translator_for(CATALOG, "fr")
        assert t.translate("common:action.save") == translator_for(CATALOG, "en").translate(
            "common:action.save"
        )

    def test_missing_key_returns_the_key(self) -> None:
        """빈 문자열보다 키가 낫다. 사용자도 우리도 무엇이 빠졌는지 안다."""
        assert translator_for(CATALOG, "en").translate("nope:missing") == "nope:missing"

    def test_notification_keys_exist_in_both_languages(self) -> None:
        """핸들러가 쓰는 키가 없으면 사용자가 키를 날것으로 보게 된다."""
        from ieum.modules.notify.handlers import ISSUE_NOTIFICATIONS

        for _kind, title_key in ISSUE_NOTIFICATIONS.values():
            namespace, key = title_key.split(":", 1)
            for locale in ("en", "ko"):
                data = json.loads(
                    (CATALOG / locale / f"{namespace}.json").read_text(encoding="utf-8")
                )
                assert key in data, f"{locale}/{namespace}.json 에 {key} 가 없다"

    def test_missing_catalog_directory_is_survivable(self, tmp_path: Path) -> None:
        """카탈로그가 없다고 알림 발송이 죽으면 안 된다."""
        t = Translator(tmp_path, "en")
        assert t.translate("common:action.save") == "common:action.save"
