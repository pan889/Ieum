"""워크플로우 엔진과 커스텀 필드 밸리데이터. DB 없이 도는 순수 단위 테스트."""

from __future__ import annotations

from typing import Any

import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.ids import new_id
from ieum.modules.issues import fields
from ieum.modules.issues.workflow import (
    Registry,
    TransitionContext,
    TransitionSpec,
    apply_post_functions,
    conditions,
    evaluate_conditions,
    post_functions,
    validate_rules,
)


def make_ctx(
    *,
    permissions: frozenset[str] = frozenset(),
    assignee: Any = None,
    reporter: Any = None,
    inputs: dict[str, Any] | None = None,
) -> TransitionContext:
    actor_id = new_id()
    return TransitionContext(
        actor_id=actor_id,
        actor_permissions=permissions,
        issue_id=new_id(),
        project_id=new_id(),
        assignee_id=actor_id if assignee == "self" else assignee,
        reporter_id=actor_id if reporter == "self" else reporter,
        from_state="Open",
        to_state="In Progress",
        to_state_category="in_progress",
        inputs=inputs or {},
    )


def spec(*, conds: list[dict[str, Any]] | None = None, posts: list[dict[str, Any]] | None = None):
    return TransitionSpec(
        id=new_id(),
        name="T",
        from_state_id=None,
        to_state_id=new_id(),
        conditions=conds or [],
        post_functions=posts or [],
    )


class TestRegistry:
    def test_unknown_rule_is_rejected_with_known_list(self) -> None:
        """모르는 규칙은 조용히 무시하지 않는다. 무시하면 '설정은 됐는데
        아무 일도 안 일어나는' 상태가 된다."""
        with pytest.raises(ValidationError) as exc:
            conditions.get("no_such_condition")
        assert exc.value.code == "issues.unknown_workflow_rule"
        assert "permission" in exc.value.details["known"]

    def test_missing_required_param_rejected_at_save_time(self) -> None:
        with pytest.raises(ValidationError) as exc:
            conditions.validate_spec({"type": "permission"})
        assert exc.value.details["missing"] == ["permission"]

    def test_spec_without_type_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            post_functions.validate_spec({"field": "x"})
        assert exc.value.code == "issues.invalid_workflow_rule"

    def test_duplicate_registration_rejected(self) -> None:
        r: Registry[Any] = Registry("테스트")
        r.register("a")(lambda *_: True)
        with pytest.raises(ValueError, match="이미 등록"):
            r.register("a")(lambda *_: True)

    def test_validate_rules_checks_both_sides(self) -> None:
        validate_rules([{"type": "is_assignee"}], [{"type": "assign_to_actor"}])
        with pytest.raises(ValidationError):
            validate_rules([], [{"type": "set_field", "field": "x"}])  # value 누락


class TestConditions:
    def test_permission(self) -> None:
        s = spec(conds=[{"type": "permission", "permission": "issue.transition"}])
        assert evaluate_conditions(s, make_ctx(permissions=frozenset({"issue.transition"}))) == []
        assert evaluate_conditions(s, make_ctx()) == ["permission"]

    def test_is_assignee(self) -> None:
        s = spec(conds=[{"type": "is_assignee"}])
        assert evaluate_conditions(s, make_ctx(assignee="self")) == []
        assert evaluate_conditions(s, make_ctx(assignee=new_id())) == ["is_assignee"]
        assert evaluate_conditions(s, make_ctx()) == ["is_assignee"]

    def test_is_reporter(self) -> None:
        s = spec(conds=[{"type": "is_reporter"}])
        assert evaluate_conditions(s, make_ctx(reporter="self")) == []
        assert evaluate_conditions(s, make_ctx(reporter=new_id())) == ["is_reporter"]

    def test_assignee_set(self) -> None:
        s = spec(conds=[{"type": "assignee_set"}])
        assert evaluate_conditions(s, make_ctx(assignee=new_id())) == []
        assert evaluate_conditions(s, make_ctx()) == ["assignee_set"]

    @pytest.mark.parametrize("value,passes", [("x", True), ("", False), (None, False), ([], False)])
    def test_field_required(self, value: Any, passes: bool) -> None:
        s = spec(conds=[{"type": "field_required", "field": "resolution"}])
        result = evaluate_conditions(s, make_ctx(inputs={"resolution": value}))
        assert (result == []) is passes

    def test_all_conditions_reported_not_just_first(self) -> None:
        """막힌 이유를 전부 줘야 UI 가 한 번에 안내할 수 있다."""
        s = spec(conds=[{"type": "is_assignee"}, {"type": "assignee_set"}])
        assert evaluate_conditions(s, make_ctx()) == ["is_assignee", "assignee_set"]


class TestPostFunctions:
    def test_set_field_with_token(self) -> None:
        s = spec(posts=[{"type": "set_field", "field": "resolved_at", "value": "$now"}])
        changes = apply_post_functions(s, make_ctx())
        assert changes[0].field == "resolved_at"
        assert changes[0].value is not None

    def test_set_field_literal(self) -> None:
        s = spec(posts=[{"type": "set_field", "field": "priority", "value": 1}])
        assert apply_post_functions(s, make_ctx())[0].value == 1

    def test_null_token_clears(self) -> None:
        s = spec(posts=[{"type": "set_field", "field": "resolved_at", "value": "$null"}])
        assert apply_post_functions(s, make_ctx())[0].value is None

    def test_assign_to_actor(self) -> None:
        ctx = make_ctx()
        s = spec(posts=[{"type": "assign_to_actor"}])
        change = apply_post_functions(s, ctx)[0]
        assert change.field == "assignee_id" and change.value == ctx.actor_id

    def test_set_progress_range_checked(self) -> None:
        s = spec(posts=[{"type": "set_progress", "value": 150}])
        with pytest.raises(ValidationError):
            apply_post_functions(s, make_ctx())

    def test_multiple_post_functions_accumulate(self) -> None:
        s = spec(
            posts=[
                {"type": "set_field", "field": "resolved_at", "value": "$now"},
                {"type": "set_progress", "value": 100},
            ]
        )
        assert [c.field for c in apply_post_functions(s, make_ctx())] == [
            "resolved_at",
            "progress",
        ]

    def test_arbitrary_string_is_not_a_token(self) -> None:
        """치환은 등록된 토큰만. 임의 표현식을 실행하지 않는다."""
        s = spec(posts=[{"type": "set_field", "field": "summary", "value": "$whatever"}])
        assert apply_post_functions(s, make_ctx())[0].value == "$whatever"


class TestFieldValidators:
    def test_all_nine_kinds_registered(self) -> None:
        assert len(fields.known_kinds()) == 9

    def test_none_means_clear(self) -> None:
        assert fields.validate_value("text", None, {}) is None

    def test_unknown_kind(self) -> None:
        with pytest.raises(ValidationError) as exc:
            fields.validate_value("nope", "x", {})
        assert exc.value.code == "issues.unknown_field_kind"

    def test_text_trims_and_bounds(self) -> None:
        assert fields.validate_value("text", "  hi  ", {}) == "hi"
        with pytest.raises(ValidationError):
            fields.validate_value("text", "x" * 11, {"max_length": 10})

    def test_number_bounds(self) -> None:
        assert fields.validate_value("number", 5, {"min": 1, "max": 10}) == 5
        with pytest.raises(ValidationError):
            fields.validate_value("number", 0, {"min": 1})
        with pytest.raises(ValidationError):
            fields.validate_value("number", 11, {"max": 10})

    def test_bool_is_not_a_number(self) -> None:
        """파이썬에서 True 는 int 다. 숫자 필드에 들어가면 안 된다."""
        with pytest.raises(ValidationError):
            fields.validate_value("number", True, {})

    def test_date_normalized_to_iso_string(self) -> None:
        from datetime import date

        assert fields.validate_value("date", date(2026, 9, 6), {}) == "2026-09-06"
        assert fields.validate_value("date", "2026-09-06", {}) == "2026-09-06"
        with pytest.raises(ValidationError):
            fields.validate_value("date", "2026/09/06", {})

    def test_select_options_enforced(self) -> None:
        config = {"options": ["a", "b"]}
        assert fields.validate_value("select", "a", config) == "a"
        with pytest.raises(ValidationError) as exc:
            fields.validate_value("select", "c", config)
        assert exc.value.details["options"] == ["a", "b"]

    def test_multi_select_dedupes_preserving_order(self) -> None:
        config = {"options": ["a", "b", "c"]}
        assert fields.validate_value("multi_select", ["c", "a", "c"], config) == ["c", "a"]

    def test_multi_select_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            fields.validate_value("multi_select", ["z"], {"options": ["a"]})

    def test_user_and_version_take_uuids(self) -> None:
        uid = new_id()
        assert fields.validate_value("user", uid, {}) == str(uid)
        assert fields.validate_value("version", str(uid), {}) == str(uid)
        with pytest.raises(ValidationError):
            fields.validate_value("user", "not-a-uuid", {})

    def test_bool_strict(self) -> None:
        assert fields.validate_value("bool", True, {}) is True
        with pytest.raises(ValidationError):
            fields.validate_value("bool", "true", {})

    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", "data:text/html,<script>", "ftp://x", "/relative"],
    )
    def test_url_scheme_whitelist(self, url: str) -> None:
        """javascript: 가 저장되면 렌더 시점에 클릭 한 번으로 XSS 가 된다."""
        with pytest.raises(ValidationError):
            fields.validate_value("url", url, {})

    def test_url_accepts_http_and_https(self) -> None:
        assert fields.validate_value("url", " https://example.com ", {}) == "https://example.com"
        assert fields.validate_value("url", "http://example.com", {}) == "http://example.com"
