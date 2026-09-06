"""커서 페이지네이션. 목록 API 는 예외 없이 이것을 쓴다 (CLAUDE.md 8절).

오프셋 페이지네이션은 쓰지 않는다: 대량 데이터에서 깊은 페이지가 느리고,
삽입·삭제 중 항목이 중복되거나 누락된다.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from ieum.core.exceptions import ValidationError

MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("커서를 해석할 수 없다.", code="common.invalid_cursor") from exc
    if not isinstance(decoded, dict):
        raise ValidationError("커서를 해석할 수 없다.", code="common.invalid_cursor")
    return decoded


@dataclass(frozen=True, slots=True)
class PageRequest:
    """목록 조회 요청. limit 상한은 강제된다."""

    limit: int = DEFAULT_LIMIT
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > MAX_LIMIT:
            raise ValidationError(
                f"limit 은 1 이상 {MAX_LIMIT} 이하여야 한다.",
                code="common.invalid_limit",
                details={"max": MAX_LIMIT},
            )

    @property
    def cursor_payload(self) -> dict[str, Any] | None:
        return decode_cursor(self.cursor) if self.cursor else None

    @property
    def fetch_limit(self) -> int:
        """다음 페이지 존재 여부를 알기 위해 1개 더 읽는다."""
        return self.limit + 1


@dataclass(frozen=True, slots=True)
class Page[T]:
    """목록 응답. total 은 요청했을 때만 계산한다 (`?with_total=1`)."""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None

    @classmethod
    def from_rows(
        cls,
        rows: list[T],
        request: PageRequest,
        cursor_of: Any,
        *,
        total: int | None = None,
    ) -> Page[T]:
        """`fetch_limit` 으로 읽어온 행에서 페이지를 만든다."""
        has_more = len(rows) > request.limit
        items = rows[: request.limit]
        next_cursor = encode_cursor(cursor_of(items[-1])) if has_more and items else None
        return cls(items=items, next_cursor=next_cursor, total=total)
