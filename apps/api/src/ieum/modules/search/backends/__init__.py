"""백엔드를 고른다 (ADR-0015).

기본값은 Postgres 다. 고르는 자리가 한 곳이어야 하는 이유: 두 곳에서 고르면
읽기와 추천이 서로 다른 백엔드를 볼 수 있고, 그러면 "검색에는 뜨는데 추천에는
안 뜬다" 가 된다.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.modules.search.backends.base import IndexedDocument, SearchBackend
from ieum.modules.search.backends.postgres import PostgresBackend


def backend_for(session: AsyncSession, settings: Settings | None = None) -> SearchBackend:
    """이 요청이 쓸 백엔드.

    요청마다 새로 만든다. Postgres 쪽은 세션에 묶여 있어 그래야 하고,
    OpenSearch 쪽은 httpx 클라이언트를 하나 여는 값이 있지만 — 전역으로
    두면 이벤트 루프가 다른 워커·시험과 섞인다(그 고장은 "다른 루프에
    붙었다" 라는 말로 나타나고 재현이 어렵다).
    """
    resolved = settings or get_settings()
    if resolved.search_backend == "opensearch":
        from ieum.modules.search.backends.opensearch import OpenSearchBackend

        return OpenSearchBackend(resolved)
    return PostgresBackend(session)


__all__ = ["IndexedDocument", "PostgresBackend", "SearchBackend", "backend_for"]
