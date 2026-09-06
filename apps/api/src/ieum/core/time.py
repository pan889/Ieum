"""시간. 서버는 항상 UTC 를 다루고, 표시만 사용자 타임존으로 한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    """timezone-aware 현재 UTC 시각."""
    return datetime.now(UTC)


def in_seconds(seconds: int) -> datetime:
    """지금부터 N초 뒤."""
    return utcnow() + timedelta(seconds=seconds)
