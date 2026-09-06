"""IQL 자동완성의 순수 부분 — 커서 자리 판단.

DB 없이 도는 것만 여기 둔다. 값 제안(프로젝트 키·사용자)은 권한을 타므로
`test_iql.py::TestSuggest` 에서 실제 DB 로 본다.
"""

from __future__ import annotations

import pytest

from ieum.modules.issues.iql.suggest import (
    analyze,
    rank,
    static_candidates,
)


def labels(source: str, cursor: int | None = None, limit: int = 50) -> list[str]:
    ctx = analyze(source, len(source) if cursor is None else cursor)
    assert ctx is not None, f"자리를 읽지 못했다: {source!r}"
    return [item.label for item in rank(ctx, static_candidates(ctx), limit)]


def kinds(source: str, cursor: int | None = None) -> set[str]:
    ctx = analyze(source, len(source) if cursor is None else cursor)
    assert ctx is not None
    return {item.kind.value for item in rank(ctx, static_candidates(ctx), 50)}


class TestFieldPosition:
    def test_empty_query_offers_fields(self) -> None:
        found = labels("")
        assert "project" in found
        assert "assignee" in found

    def test_prefix_narrows(self) -> None:
        assert labels("pro") == ["progress", "project", "statuscategory"]

    def test_after_and_offers_fields_again(self) -> None:
        assert "status" in labels('project = "ENG" AND ')

    def test_order_by_offers_only_sortable_fields(self) -> None:
        found = labels("priority > 3 ORDER BY ")
        assert "created" in found
        # 라벨은 값이 여럿이라 정렬 기준이 없다. 골라도 거절당한다.
        assert "labels" not in found
        assert "project" not in found

    def test_sort_direction_after_sort_key(self) -> None:
        assert labels("ORDER BY created ") == ["ASC", "DESC"]


class TestOperatorPosition:
    def test_operators_are_narrowed_to_the_field(self) -> None:
        """bool 필드에 `>` 를 제안하면 고르는 순간 오류가 난다."""
        assert labels("archived ") == ["=", "!="]

    def test_equality_comes_first(self) -> None:
        """알파벳순이면 `!=` 가 `=` 을 앞선다 — 무심코 Enter 를 친 사람이
        정반대 조건을 얻는다."""
        assert labels("summary ")[0] == "="

    def test_date_field_offers_ordering(self) -> None:
        assert labels("created ") == ["=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN"]

    def test_text_field_offers_contains(self) -> None:
        assert "~" in labels("summary ")

    def test_half_typed_operator(self) -> None:
        assert labels("summary !") == ["!=", "!~"]

    def test_emptiness_comes_as_one_step(self) -> None:
        """`IS` 와 `EMPTY` 는 늘 붙어 다닌다. 두 번 고르게 하지 않는다."""
        found = labels("assignee ")
        assert "IS EMPTY" in found
        assert "IS NOT EMPTY" in found

    def test_not_null_field_hides_emptiness(self) -> None:
        # summary 는 NOT NULL 이다 — 비어 있을 수 없다.
        assert "IS EMPTY" not in labels("summary ")

    def test_after_is_the_not_is_emptiness_not_a_list(self) -> None:
        found = labels("assignee IS ")
        assert "NOT" in found
        assert "NOT IN" not in found


class TestValuePosition:
    def test_bool_field_offers_true_false(self) -> None:
        assert labels("archived = ") == ["false", "true"]

    def test_user_field_offers_current_user(self) -> None:
        assert "currentUser()" in labels("assignee = ")

    def test_date_field_offers_date_functions(self) -> None:
        found = labels("created > ")
        assert "startOfDay()" in found
        assert "currentUser()" not in found

    def test_function_prefix_matches(self) -> None:
        assert labels("created > start") == [
            "startOfDay()",
            "startOfMonth()",
            "startOfWeek()",
        ]

    def test_caret_stops_inside_functions_that_take_arguments(self) -> None:
        ctx = analyze("created > ", 10)
        assert ctx is not None
        by_label = {i.label: i for i in static_candidates(ctx)}
        # `startOfDay(±n)` — 인자를 받는 함수는 괄호 안에서 멈춘다.
        assert by_label["startOfDay()"].caret_at() == len("startOfDay(")
        # 인자가 없는 함수는 괄호를 지나간다.
        assert by_label["now()"].caret_at() == len("now()")

    def test_in_opens_a_parenthesis_first(self) -> None:
        """`x IN "A"` 는 문법은 통과해도 컴파일에서 거절된다."""
        assert labels("labels IN ") == ["("]

    def test_keyword_after_complete_condition(self) -> None:
        assert set(labels('project = "ENG" ')) == {"AND", "OR", "ORDER BY"}


class TestReplaceRange:
    def test_word_prefix_is_replaced(self) -> None:
        ctx = analyze("pro", 3)
        assert ctx is not None
        assert (ctx.start, ctx.end) == (0, 3)

    def test_open_quote_is_not_retyped(self) -> None:
        ctx = analyze('project = "EN', 13)
        assert ctx is not None
        # 열어 둔 따옴표부터 갈아 끼운다 — 안 그러면 `""ENG"` 가 된다.
        assert (ctx.start, ctx.prefix, ctx.quote) == (10, "EN", '"')

    def test_closing_quote_is_swallowed(self) -> None:
        source = 'project = "EN"'
        ctx = analyze(source, 13)
        assert ctx is not None
        assert (ctx.start, ctx.end) == (10, 14)

    def test_cursor_in_the_middle_ignores_the_tail(self) -> None:
        source = 'pro = "ENG"'
        ctx = analyze(source, 3)
        assert ctx is not None
        assert ctx.prefix == "pro"
        assert (ctx.start, ctx.end) == (0, 3)


class TestBrokenInput:
    def test_garbage_offers_nothing_rather_than_guessing(self) -> None:
        assert analyze("project = = =", 13) is None

    def test_overlong_query_is_refused(self) -> None:
        source = "a" * 5000
        assert analyze(source, 5000) is None

    @pytest.mark.parametrize("cursor", [-5, 0, 3, 9999])
    def test_cursor_outside_the_text_does_not_crash(self, cursor: int) -> None:
        analyze("project", cursor)


class TestRanking:
    def test_values_come_before_keywords(self) -> None:
        assert labels("archived = ")[0] in {"false", "true"}

    def test_inner_match_is_kept_but_ranked_lower(self) -> None:
        found = labels("created > user", limit=50)
        # `user` 로 currentUser() 를 찾을 수 있어야 한다 — 다만 날짜 필드라
        # 여기서는 반환형이 맞지 않아 걸러진다.
        assert found == []
        assert "currentUser()" in labels("assignee = user")

    def test_kinds_are_tagged(self) -> None:
        assert kinds("") == {"field", "keyword"}
