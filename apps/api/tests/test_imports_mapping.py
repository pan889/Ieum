"""소스의 낱말을 이 설치본의 어휘로 옮기는 규칙.

**이 파일이 지키는 것은 "조용히 틀리지 않는다" 하나다.** 이관은 되돌리기
번거로우니, 못 이은 것을 아무 데나 넣는 것보다 못 이었다고 말하는 쪽이 낫다.
"""

from __future__ import annotations

from typing import ClassVar

from ieum.migrate.archive import Issue
from ieum.modules.imports.mapping import (
    BY_NAME,
    BY_OVERRIDE,
    BY_RANK,
    UNMATCHED,
    Report,
    Target,
    match_names,
    match_priorities,
    normalize,
    used_vocabulary,
)


class TestNamesAreComparedLoosely:
    def test_case_and_spaces_do_not_matter(self) -> None:
        assert normalize("In Progress") == normalize("in  progress")

    def test_korean_spacing_does_not_matter(self) -> None:
        """`진행중` 과 `진행 중` 은 사람에게 같은 낱말이다."""
        assert normalize("진행중") == normalize("진행 중")

    def test_letters_are_left_alone(self) -> None:
        """ASCII 로 접지 않는다 — 한국어 이름이 뭉개진다."""
        assert normalize("버그") == "버그"
        assert normalize("Bug") != normalize("버그")


class TestMatchingByName:
    TARGETS: ClassVar[list[Target]] = [
        Target("t1", "Bug"),
        Target("t2", "Task"),
        Target("t3", "진행 중"),
    ]

    def test_the_same_name_is_linked(self) -> None:
        [match] = match_names(("bug",), self.TARGETS)
        assert (match.target_id, match.how) == ("t1", BY_NAME)

    def test_a_name_we_do_not_have_is_left_unmatched(self) -> None:
        """짐작해서 아무 데나 넣지 않는다."""
        [match] = match_names(("Feature",), self.TARGETS)
        assert (match.target_id, match.how, match.ok) == ("", UNMATCHED, False)

    def test_a_person_can_pair_it_by_hand(self) -> None:
        [match] = match_names(("Feature",), self.TARGETS, {"Feature": "t2"})
        assert (match.target_id, match.how) == ("t2", BY_OVERRIDE)

    def test_an_override_pointing_nowhere_is_ignored_not_obeyed(self) -> None:
        """없는 id 를 주면 그것을 쓰는 대신 못 이은 것으로 둔다."""
        [match] = match_names(("bug",), self.TARGETS, {"bug": "없는id"})
        assert match.how == BY_NAME

    def test_an_ambiguous_name_is_not_linked(self) -> None:
        """**이 시험이 이 파일의 이유다.**

        같은 이름의 상태가 워크플로우마다 따로 있다(`StateRef.workflow_name`
        이 존재하는 이유). 아무거나 고르면 그 이슈들은 사람이 안 고른
        워크플로우로 들어가고, 화면에는 그 사실이 안 나타난다.
        """
        targets = [
            Target("s1", "대기", qualifier="버그 워크플로우"),
            Target("s2", "대기", qualifier="문의 워크플로우"),
        ]
        [match] = match_names(("대기",), targets)
        assert match.ok is False

    def test_the_person_can_still_choose_when_it_is_ambiguous(self) -> None:
        targets = [Target("s1", "대기", "버그"), Target("s2", "대기", "문의")]
        [match] = match_names(("대기",), targets, {"대기": "s2"})
        assert (match.target_id, match.how) == ("s2", BY_OVERRIDE)


class TestPriorities:
    """이름을 우리 1~5 에 편다.

    **아는 이름이면 이름으로 잇는다.** 예전에는 순서만 봤는데, 그 순서는
    소스가 정한 순서가 아니라 묶음 안에서 **이슈가 나온 순서**였다. 그래서
    첫 이슈가 High 면 High 가 5(가장 낮음)가 됐다.
    """

    def test_the_order_they_appear_in_does_not_decide_anything(self) -> None:
        """**이 파일의 이유다.** 나온 순서가 뒤집혀도 뜻은 그대로여야 한다."""
        forwards = match_priorities(("Low", "Normal", "High"))
        backwards = match_priorities(("High", "Normal", "Low"))
        assert {m.source: m.target_id for m in forwards} == {
            "Low": "4",
            "Normal": "3",
            "High": "2",
        }
        assert {m.source: m.target_id for m in forwards} == {
            m.source: m.target_id for m in backwards
        }

    def test_jira_defaults_land_on_all_five(self) -> None:
        matches = match_priorities(("Lowest", "Low", "Medium", "High", "Highest"))
        assert [m.target_id for m in matches] == ["5", "4", "3", "2", "1"]
        assert {m.how for m in matches} == {BY_NAME}

    def test_redmine_defaults_keep_their_direction(self) -> None:
        """**겹치는 자리를 허용한다.** 소스가 우리보다 잘게 나눠 두면 몇 개는
        같은 자리로 모인다 — 방향이 맞는 것이 자리 수가 맞는 것보다 낫다."""
        matches = match_priorities(("Low", "Normal", "High", "Urgent", "Immediate"))
        assert [m.target_id for m in matches] == ["4", "3", "2", "2", "1"]
        assert {m.how for m in matches} == {BY_NAME}

    def test_names_are_matched_case_and_space_insensitively(self) -> None:
        assert [m.target_id for m in match_priorities((" HIGH ", "low"))] == ["2", "4"]

    def test_unknown_names_fall_back_to_the_spread(self) -> None:
        """하나도 못 알아보면 옛 방식대로 편다. 아무 짝도 안 지어 주는 것보다 낫다."""
        matches = match_priorities(("가장낮음", "가운데", "가장높음"))
        assert [m.target_id for m in matches] == ["5", "3", "1"]
        assert {m.how for m in matches} == {BY_RANK}

    def test_a_half_known_list_uses_the_spread_for_all_of_it(self) -> None:
        """두 규칙이 한 목록 안에 섞이면 서로 어긋난다."""
        assert {m.how for m in match_priorities(("High", "긴급해요"))} == {BY_RANK}

    def test_one_name_is_normal_not_highest(self) -> None:
        """하나뿐이면 순서가 없다. 전부 최고로 만들면 목록이 뜻을 잃는다."""
        assert match_priorities(("아무거나",))[0].target_id == "3"

    def test_a_person_can_pin_a_rank(self) -> None:
        [match] = match_priorities(("Immediate",), {"Immediate": "1"})
        assert (match.target_id, match.how) == ("1", BY_OVERRIDE)

    def test_a_rank_outside_one_to_five_is_refused(self) -> None:
        """사람이 준 값이라도 범위를 벗어나면 안 쓴다."""
        [match] = match_priorities(("Immediate",), {"Immediate": "9"})
        assert (match.target_id, match.how) == ("1", BY_NAME)

    def test_it_never_leaves_a_priority_unmatched(self) -> None:
        """우선순위는 못 이어도 이슈를 막지 않는다 — 3 으로 들어가면 된다."""
        assert all(m.ok for m in match_priorities(("무엇이든", "아무거나")))


def _issue(source_id: str, **kwargs: str) -> Issue:
    return Issue(source_id=source_id, summary="x", **kwargs)


class TestOnlyTheWordsActuallyUsed:
    """소스가 상태를 스무 개 정의하고 셋만 썼으면 **셋만** 짝지으면 된다."""

    def test_it_collects_in_order_without_repeats(self) -> None:
        vocabulary = used_vocabulary(
            [
                _issue("1", type="Bug", status="New", priority="Low"),
                _issue("2", type="Task", status="New", priority="High"),
                _issue("3", type="Bug", status="Closed", priority="Low"),
            ]
        )
        assert vocabulary.types == ("Bug", "Task")
        assert vocabulary.statuses == ("New", "Closed")
        assert vocabulary.priorities == ("Low", "High")

    def test_blank_words_are_not_words(self) -> None:
        vocabulary = used_vocabulary([_issue("1", type="", status="  ", priority="Low")])
        assert (vocabulary.types, vocabulary.statuses) == ((), ())
        assert vocabulary.priorities == ("Low",)


class TestWhatTheReportCallsBlocking:
    def test_an_unmatched_type_or_status_blocks_the_issue(self) -> None:
        """이 둘이 없으면 이슈를 만들 수가 없다 — 둘 다 NOT NULL 이다."""
        report = Report(
            types=match_names(("Feature",), [Target("t1", "Bug")]),
            statuses=match_names(("New",), [Target("s1", "열림")]),
        )
        assert report.blocking == ["종류: Feature", "상태: New"]

    def test_an_unmatched_priority_does_not_block(self) -> None:
        report = Report(priorities=match_priorities(("아무거나",)))
        assert report.blocking == []
