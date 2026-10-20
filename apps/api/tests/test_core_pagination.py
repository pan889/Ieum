"""core.pagination — 커서 페이지네이션."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from ieum.core.exceptions import ValidationError
from ieum.core.pagination import (
    MAX_LIMIT,
    Page,
    PageRequest,
    aware_datetime,
    cursor_field,
    decode_cursor,
    encode_cursor,
)


class TestPageRequest:
    def test_limit_upper_bound_enforced(self) -> None:
        """상한을 넘기면 조용히 잘라내지 않고 거부한다."""
        with pytest.raises(ValidationError) as exc:
            PageRequest(limit=MAX_LIMIT + 1)
        assert exc.value.code == "common.invalid_limit"

    @pytest.mark.parametrize("bad", [0, -1])
    def test_limit_lower_bound_enforced(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            PageRequest(limit=bad)

    def test_fetch_limit_reads_one_extra(self) -> None:
        """다음 페이지 존재 여부를 알려면 limit+1 을 읽어야 한다."""
        assert PageRequest(limit=20).fetch_limit == 21

    def test_invalid_cursor_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _ = PageRequest(cursor="!!!not-base64!!!").cursor_payload
        assert exc.value.code == "common.invalid_cursor"


class TestCursor:
    def test_roundtrip(self) -> None:
        payload = {"id": "abc", "updated_at": "2026-09-06T00:00:00Z"}
        assert decode_cursor(encode_cursor(payload)) == payload

    def test_no_padding_in_cursor(self) -> None:
        """URL 에 그대로 실리므로 '=' 패딩을 남기지 않는다."""
        assert "=" not in encode_cursor({"id": "a"})


class TestPage:
    def test_next_cursor_present_when_more_rows(self) -> None:
        req = PageRequest(limit=2)
        page: Page[int] = Page.from_rows([1, 2, 3], req, lambda r: {"id": r})
        assert page.items == [1, 2]
        assert page.next_cursor is not None
        assert decode_cursor(page.next_cursor) == {"id": 2}

    def test_next_cursor_absent_on_last_page(self) -> None:
        req = PageRequest(limit=5)
        page: Page[int] = Page.from_rows([1, 2], req, lambda r: {"id": r})
        assert page.items == [1, 2]
        assert page.next_cursor is None

    def test_empty_result(self) -> None:
        page: Page[int] = Page.from_rows([], PageRequest(), lambda r: {"id": r})
        assert page.items == [] and page.next_cursor is None

    def test_total_is_opt_in(self) -> None:
        """total 은 요청했을 때만 계산한다. 기본은 None."""
        assert Page.from_rows([1], PageRequest(), lambda r: {"id": r}).total is None
        assert Page.from_rows([1], PageRequest(), lambda r: {"id": r}, total=1).total == 1


class TestCursorField:
    """커서 안의 값이 틀리면 500 이 아니라 `common.invalid_cursor` 다.

    디코딩이 됐다고 안에 든 것이 맞다는 보장은 없다 — 클라이언트가 들고
    다니다 돌려주는 값이다.
    """

    def test_reads_a_good_value(self) -> None:
        uid = "01929d4e-0000-7000-8000-000000000001"
        assert cursor_field({"id": uid}, "id", UUID) == UUID(uid)
        assert cursor_field({"t": "2026-09-06T00:00:00+00:00"}, "t", aware_datetime) == datetime(
            2026, 9, 6, tzinfo=UTC
        )

    @pytest.mark.parametrize(
        ("payload", "parse"),
        [
            ({}, UUID),
            ({"id": "nope"}, UUID),
            ({"id": 7}, UUID),
            ({"id": None}, UUID),
            ({"id": "yesterday"}, aware_datetime),
            ({"id": 7}, aware_datetime),
            ({"id": "2026-09-06T00:00:00"}, aware_datetime),
        ],
    )
    def test_a_bad_value_is_an_invalid_cursor(self, payload: dict[str, Any], parse: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            cursor_field(payload, "id", parse)
        assert exc.value.code == "common.invalid_cursor"
