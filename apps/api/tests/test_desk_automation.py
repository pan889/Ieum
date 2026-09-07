"""자동화 규칙의 조건과 검증 (feature-map C9).

순수 함수라 값을 손으로 적어 못 박는다. 행을 쓰는 층은 `test_desk_rules.py`.

붙잡는 것:

- **저장되고 실행만 실패하는 규칙을 만들지 않는다.** 자동화의 실패는 조용하다
  — 아무 일도 안 일어난 것과 구별되지 않는다.
- **조건은 전부 맞아야 한다(AND).** OR 은 규칙을 둘로 나눠 표현한다.
- **조치가 없는 규칙은 규칙이 아니다.** 조건만 맞춰 보고 아무 것도 안 한다.
- **UUID 는 문자열로 온다.** JSON 에 UUID 타입이 없으므로 비교가 그것을 알아야
  한다 — 모르면 대소문자가 다른 같은 id 가 다른 것이 된다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from ieum.modules.desk.automation import (
    AutomationError,
    TicketFacts,
    matches,
    parse_action,
    validate_actions,
    validate_conditions,
    validate_trigger,
)


def facts(**overrides: object) -> TicketFacts:
    base: dict[str, object] = {
        "priority": 3,
        "channel": "portal",
        "summary": "프린터가 안 됩니다",
        "state_category": "todo",
    }
    base.update(overrides)
    return TicketFacts(**base)  # type: ignore[arg-type]


class TestRefusingABrokenRule:
    def test_a_good_rule_passes_through(self) -> None:
        assert validate_trigger({"event": "desk.ticket.submitted"}) == {
            "event": "desk.ticket.submitted"
        }
        conditions = [{"field": "priority", "op": "gte", "value": 4}]
        assert validate_conditions(conditions) == conditions
        actions = [{"kind": "set_priority", "priority": 5}]
        assert validate_actions(actions) == actions

    @pytest.mark.parametrize(
        "trigger",
        [{"event": "issue.archived"}, {"event": None}, {}, {"event": "issue.commented", "x": 1}],
    )
    def test_an_unknown_trigger_is_refused(self, trigger: dict[str, object]) -> None:
        with pytest.raises(AutomationError):
            validate_trigger(trigger)

    def test_no_conditions_means_always(self) -> None:
        """ "이 이벤트면 언제나" 는 흔한 규칙이다."""
        assert validate_conditions([]) == []
        assert validate_conditions(None) == []
        assert matches([], facts()) is True

    @pytest.mark.parametrize(
        "condition",
        [
            {"field": "nope", "op": "eq", "value": 1},
            {"field": "priority", "op": "matches", "value": 1},
            {"field": "priority", "op": "eq"},  # 비교할 값이 없다
            {"field": "priority", "op": "in", "value": 3},  # 목록이어야 한다
            {"field": "priority", "op": "gte", "value": "높음"},
            {"field": "summary", "op": "contains", "value": 3},
            {"field": "priority", "op": "eq", "value": 1, "extra": True},
        ],
    )
    def test_a_condition_that_cannot_run_is_refused(self, condition: dict[str, object]) -> None:
        with pytest.raises(AutomationError):
            validate_conditions([condition])

    def test_too_many_conditions_is_refused(self) -> None:
        """넘으면 규칙이 아니라 질의다 — 그건 큐가 한다."""
        many = [{"field": "priority", "op": "eq", "value": n} for n in range(11)]
        with pytest.raises(AutomationError):
            validate_conditions(many)

    def test_a_rule_without_actions_is_refused(self) -> None:
        """조건만 맞춰 보고 아무 것도 안 하는 규칙은 저장되면 안 된다."""
        for actions in ([], None, "설정"):
            with pytest.raises(AutomationError):
                validate_actions(actions)

    @pytest.mark.parametrize(
        "action",
        [
            {"kind": "delete_ticket"},
            {"kind": "set_priority"},  # 몇으로?
            {"kind": "set_priority", "priority": 0},
            {"kind": "set_priority", "priority": 6},
            {"kind": "set_priority", "priority": True},
            {"kind": "assign"},  # 누구에게?
            {"kind": "assign", "user_id": "not-a-uuid"},
            {"kind": "reply_with_canned"},  # 무슨 문구로?
            {"kind": "add_note", "canned_response_id": "nope"},
            {"kind": "assign", "user_id": str(uuid4()), "extra": 1},
        ],
    )
    def test_an_action_that_cannot_run_is_refused(self, action: dict[str, object]) -> None:
        with pytest.raises(AutomationError):
            validate_actions([action])

    def test_the_same_action_twice_is_refused(self) -> None:
        """우선순위를 3 으로 정하고 5 로도 정하는 규칙은 뜻이 없고, 어느 쪽이
        이기는지는 배열 순서라는 보이지 않는 규칙이 된다."""
        with pytest.raises(AutomationError):
            validate_actions(
                [{"kind": "set_priority", "priority": 3}, {"kind": "set_priority", "priority": 5}]
            )

    def test_different_actions_together_are_fine(self) -> None:
        actions = [
            {"kind": "set_priority", "priority": 5},
            {"kind": "assign", "user_id": str(uuid4())},
        ]
        assert len(validate_actions(actions)) == 2


class TestRefusingAValueThatCanNeverMatch:
    """**저장은 되고 영원히 안 맞는 조건을 막는다.**

    이 층이 없으면 화면에서 구별할 방법이 없다: 규칙이 안 돈 것과 조건이 안
    맞은 것이 똑같이 "아무 일도 안 일어남" 으로 보인다.
    """

    def test_a_boolean_field_refuses_the_string_true(self) -> None:
        # `_same` 이 불리언을 `is` 로 비교하므로 `True is "true"` 는 거짓이다.
        # 문자열로 두면 규칙은 저장되고 한 번도 안 걸린다.
        with pytest.raises(AutomationError, match="참·거짓"):
            validate_conditions([{"field": "is_internal", "op": "eq", "value": "true"}])
        assert validate_conditions([{"field": "is_internal", "op": "eq", "value": True}])

    def test_a_number_field_refuses_a_string(self) -> None:
        with pytest.raises(AutomationError, match="정수"):
            validate_conditions([{"field": "priority", "op": "eq", "value": "4"}])

    def test_priority_refuses_a_boolean(self) -> None:
        # `True` 는 파이썬에서 `int` 다 — 우선순위가 참이 되게 두지 않는다.
        with pytest.raises(AutomationError, match="정수"):
            validate_conditions([{"field": "priority", "op": "eq", "value": True}])

    def test_size_comparison_refuses_a_text_field(self) -> None:
        # 문자열에 걸면 `_one_matches` 가 언제나 거짓을 낸다.
        with pytest.raises(AutomationError, match="쓸 수 없다"):
            validate_conditions([{"field": "summary", "op": "gte", "value": 3}])

    def test_contains_refuses_a_uuid_field(self) -> None:
        # 우연히 겹치는 조각으로 맞는다 — 뜻이 없다.
        with pytest.raises(AutomationError, match="쓸 수 없다"):
            validate_conditions([{"field": "request_type_id", "op": "contains", "value": "ab"}])

    def test_a_typo_in_an_enum_is_refused(self) -> None:
        with pytest.raises(AutomationError, match="모르는 경로"):
            validate_conditions([{"field": "channel", "op": "eq", "value": "Portal"}])
        with pytest.raises(AutomationError, match="모르는 상태 분류"):
            validate_conditions([{"field": "state_category", "op": "eq", "value": "inprogress"}])

    def test_a_uuid_field_refuses_junk_and_canonicalises(self) -> None:
        with pytest.raises(AutomationError, match="UUID"):
            validate_conditions([{"field": "organization_id", "op": "eq", "value": "nope"}])
        raw = uuid4()
        [row] = validate_conditions(
            [{"field": "organization_id", "op": "eq", "value": str(raw).upper()}]
        )
        # 화면이 고른 값을 되돌려 받아 `select` 와 비교한다. 대문자로 저장되면
        # 아무 것도 안 골라진 것처럼 보인다.
        assert row["value"] == str(raw)

    def test_in_checks_every_item(self) -> None:
        with pytest.raises(AutomationError, match="모르는 경로"):
            validate_conditions(
                [{"field": "channel", "op": "in", "value": ["portal", "carrier pigeon"]}]
            )

    def test_an_empty_in_is_refused(self) -> None:
        # 아무 것도 안 맞는 조건이다. 규칙 전체가 조용히 죽는다.
        with pytest.raises(AutomationError, match="비어 있다"):
            validate_conditions([{"field": "channel", "op": "in", "value": []}])

    def test_the_vocabularies_match_the_modules_that_own_them(self) -> None:
        """**손으로 두 번 적은 목록을 못 박는다.**

        이 층은 순수하게 두려고 원본을 안 부른다. 원본에 하나 늘면 여기가
        붉어져야 한다 — 안 그러면 새 경로를 고른 조건이 조용히 거절된다.
        """
        from ieum.modules.desk.automation import CATEGORIES, CHANNELS
        from ieum.modules.desk.models import TICKET_CHANNELS
        from ieum.modules.issues.contracts import STATE_CATEGORIES

        assert CHANNELS == TICKET_CHANNELS
        assert CATEGORIES == STATE_CATEGORIES

    def test_every_field_has_a_kind_and_every_kind_has_operators(self) -> None:
        """항목을 늘리면서 종류를 안 적으면 `KeyError` 로 터진다."""
        from ieum.modules.desk.automation import FIELD_KINDS, FIELDS, OPERATORS, OPS_FOR_KIND

        assert set(FIELD_KINDS) == set(FIELDS)
        for kind in set(FIELD_KINDS.values()):
            assert OPS_FOR_KIND[kind]
            assert set(OPS_FOR_KIND[kind]) <= set(OPERATORS)


class TestMatching:
    def test_equality(self) -> None:
        assert matches([{"field": "channel", "op": "eq", "value": "portal"}], facts()) is True
        assert matches([{"field": "channel", "op": "eq", "value": "email"}], facts()) is False

    def test_inequality(self) -> None:
        assert matches([{"field": "channel", "op": "ne", "value": "email"}], facts()) is True

    @pytest.mark.parametrize(
        ("op", "value", "expected"),
        [("gte", 3, True), ("gte", 4, False), ("lte", 3, True), ("lte", 2, False)],
    )
    def test_size(self, op: str, value: int, expected: bool) -> None:
        assert matches([{"field": "priority", "op": op, "value": value}], facts()) is expected

    def test_size_on_a_string_is_false_not_a_crash(self) -> None:
        """사전 순 비교는 관리자가 뜻한 것이 아니다. 터지지도 않는다 —
        워커가 규칙 하나로 죽으면 다른 티켓의 자동화도 멈춘다."""
        assert matches([{"field": "channel", "op": "gte", "value": 3}], facts()) is False

    def test_membership(self) -> None:
        condition = {"field": "channel", "op": "in", "value": ["email", "portal"]}
        assert matches([condition], facts()) is True
        assert matches([condition], facts(channel="agent")) is False

    def test_contains_ignores_case(self) -> None:
        assert matches([{"field": "summary", "op": "contains", "value": "프린터"}], facts())
        assert matches(
            [{"field": "summary", "op": "contains", "value": "PRINTER"}],
            facts(summary="Printer broken"),
        )

    def test_all_conditions_must_match(self) -> None:
        """**AND 다.** 하나라도 어긋나면 규칙은 안 돈다."""
        both = [
            {"field": "channel", "op": "eq", "value": "portal"},
            {"field": "priority", "op": "gte", "value": 4},
        ]
        assert matches(both, facts()) is False
        assert matches(both, facts(priority=5)) is True

    def test_a_uuid_compares_as_a_uuid(self) -> None:
        """**JSON 에 UUID 타입이 없다.** 문자열끼리 비교하면 대소문자가 다른
        같은 id 가 다른 것이 된다."""
        org = uuid4()
        condition = {"field": "organization_id", "op": "eq", "value": str(org).upper()}
        assert matches([condition], facts(organization_id=org)) is True

    def test_an_absent_uuid_does_not_match(self) -> None:
        condition = {"field": "organization_id", "op": "eq", "value": str(uuid4())}
        assert matches([condition], facts(organization_id=None)) is False

    def test_a_boolean_is_not_a_number(self) -> None:
        """`True == 1` 이 참이라, 안 막으면 우선순위 1 이 "내부 노트임" 과
        같아진다."""
        assert matches([{"field": "is_internal", "op": "eq", "value": 1}], facts()) is False
        assert (
            matches([{"field": "is_internal", "op": "eq", "value": True}], facts(is_internal=True))
            is True
        )

    def test_event_facts_are_visible(self) -> None:
        """ "방금 done 으로 갔다" 는 저장된 상태만으로는 알 수 없다."""
        condition = {"field": "to_state_category", "op": "eq", "value": "done"}
        assert matches([condition], facts(to_state_category="done")) is True
        assert matches([condition], facts()) is False


class TestParsingAnAction:
    def test_it_reads_the_stored_shape(self) -> None:
        who = uuid4()
        parsed = parse_action({"kind": "assign", "user_id": str(who)})
        assert parsed.kind == "assign"
        assert parsed.user_id == who
        assert parsed.priority is None

    def test_a_priority_action_carries_its_number(self) -> None:
        parsed = parse_action({"kind": "set_priority", "priority": 5})
        assert parsed.priority == 5
        assert parsed.user_id is None

    def test_a_canned_action_carries_its_id(self) -> None:
        canned = uuid4()
        parsed = parse_action({"kind": "add_note", "canned_response_id": str(canned)})
        assert isinstance(parsed.canned_response_id, UUID)
        assert parsed.canned_response_id == canned
