"""식별자. PK 는 UUIDv7 (D-10) — 시간 정렬 가능하고 노출해도 안전하다."""

from __future__ import annotations

import secrets
from uuid import UUID

from uuid6 import uuid7


def new_id() -> UUID:
    """새 UUIDv7 을 만든다."""
    return uuid7()


def new_token(nbytes: int = 32) -> str:
    """세션·API 토큰용 URL-safe 무작위 문자열."""
    return secrets.token_urlsafe(nbytes)
