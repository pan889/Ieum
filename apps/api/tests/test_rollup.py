"""진행률 롤업. 부모 값은 자식에서 계산한다."""

from __future__ import annotations

import pytest

from ieum.core.ids import new_id
from ieum.modules.issues.models import Issue
from ieum.modules.issues.rollup import weighted_progress


def _child(progress: int, estimate: int | None = None) -> Issue:
    return Issue(
        id=new_id(),
        project_id=new_id(),
        key_seq=1,
        type_id=new_id(),
        state_id=new_id(),
        summary="c",
        progress=progress,
        estimate_minutes=estimate,
    )


class TestWeighting:
    def test_equal_weight_when_estimates_are_missing(self) -> None:
        assert weighted_progress([_child(0), _child(100)]) == 50
        assert weighted_progress([_child(0), _child(50), _child(100)]) == 50

    def test_weighted_by_estimate_when_all_have_one(self) -> None:
        # 큰 일이 절반 끝난 것과 작은 일이 다 끝난 것은 같지 않다.
        assert weighted_progress([_child(100, 60), _child(0, 540)]) == 10

    def test_mixed_estimates_fall_back_to_equal_weight(self) -> None:
        """없는 추정을 0 으로 치면 그 자식이 계산에서 사라진다.

        90% 라고 표시되는데 실제로는 절반이 손도 안 댄 상태가 된다.
        """
        assert weighted_progress([_child(100, 600), _child(0, None)]) == 50

    def test_zero_estimate_is_treated_as_missing(self) -> None:
        # 0분짜리 일은 가중치가 없다. 나누면 0 으로 나눈다.
        assert weighted_progress([_child(100, 0), _child(0, 60)]) == 50

    def test_rounds_to_a_whole_percent(self) -> None:
        assert weighted_progress([_child(100), _child(0), _child(0)]) == 33
        assert weighted_progress([_child(100), _child(100), _child(0)]) == 67

    @pytest.mark.parametrize("progress", [0, 100])
    def test_single_child_mirrors_it(self, progress: int) -> None:
        assert weighted_progress([_child(progress)]) == progress
