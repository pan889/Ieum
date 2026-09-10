"""통합 검색 (ADR-0005).

이슈와 문서를 한 상자에서 찾는다. 색인은 원본과 같은 트랜잭션에서 갱신하고,
권한은 **질의에 얹어서** 거른다 — 가져와서 거르면 페이지가 어긋나고 한 군데만
빠뜨려도 유출이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.permissions import Acl, PermissionService
from ieum.modules.search.backends import backend_for
from ieum.modules.search.backends.base import IndexedDocument
from ieum.modules.search.models import DOCUMENT_KINDS

#: 종류별로 필요한 권한. 이 모듈은 남의 권한 상수를 import 하지 않는다
#: (모듈 경계) — 문자열은 어차피 레지스트리에 등록된 같은 값이다.
KIND_PERMISSIONS = {"issue": "issue.view", "page": "wiki.page.view"}

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
#: 발췌 길이. 너무 길면 목록이 아니라 본문이 된다.
SNIPPET_WIDTH = 160

_WORD = re.compile(r"[^\s\"'()]+")
#: 질의 구문 낱말. 발췌를 만들 때 검색어로 치지 않는다.
_OPERATORS = frozenset({"OR", "AND", "NOT", "-"})


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: str
    entity_id: UUID
    ref: str
    title: str
    snippet: str
    scope_id: UUID
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SearchResults:
    hits: list[SearchHit]
    total: int
    #: 화면이 굵게 칠할 낱말. 서버가 HTML 을 만들어 보내지 않는다 —
    #: 그러면 본문에 HTML 을 흘려보내는 통로가 하나 더 생긴다.
    keywords: list[str]


class SearchService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def search(
        self,
        actor: Actor,
        *,
        query: str,
        kinds: tuple[str, ...] = DOCUMENT_KINDS,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> SearchResults:
        text = query.strip()
        if not text:
            return SearchResults(hits=[], total=0, keywords=[])

        wanted = tuple(k for k in kinds if k in DOCUMENT_KINDS) or DOCUMENT_KINDS
        acls = {kind: await self._acl(actor, kind) for kind in wanted}
        # **부를 때 만들고 닫는다.** `__init__` 에서 만들어 두면 두 번
        # 검색하는 요청이 닫힌 클라이언트를 쓴다. OpenSearch 백엔드는
        # 소켓을 여므로 안 닫으면 요청 수만큼 쌓이고, 그건 한참 뒤에
        # "파일 핸들이 없다" 로만 나타난다. Postgres 쪽은 할 일이 없다.
        backend = backend_for(self._s)
        try:
            rows, total = await backend.search(
                query=text,
                acls=acls,
                principal_ids=actor.principal_ids,
                kinds=wanted,
                limit=min(limit, MAX_LIMIT),
                offset=max(offset, 0),
            )
        finally:
            await backend.aclose()
        keywords = _keywords(text)
        return SearchResults(
            hits=[_hit(row, keywords) for row in rows], total=total, keywords=keywords
        )

    async def _acl(self, actor: Actor, kind: str) -> Acl:
        return await self._perms.acl_for(self._s, actor, KIND_PERMISSIONS[kind])


def _keywords(query: str) -> list[str]:
    """질의에서 낱말만 뽑는다. 연산자는 뺀다.

    PGroonga 에도 `pgroonga_query_extract_keywords` 가 있지만, 그걸 쓰려면
    발췌마다 DB 를 한 번 더 부른다. 여기 규칙으로 충분하다.
    """
    words = [w for w in _WORD.findall(query) if w not in _OPERATORS and len(w) > 1]
    return list(dict.fromkeys(words))[:10]


def _hit(row: IndexedDocument, keywords: list[str]) -> SearchHit:
    return SearchHit(
        kind=row.kind,
        entity_id=row.entity_id,
        ref=row.ref,
        title=row.title,
        snippet=_snippet(row.body, keywords),
        scope_id=row.scope_id,
        updated_at=row.source_updated_at,
    )


def _snippet(body: str, keywords: list[str]) -> str:
    """검색어 둘레를 잘라 온다. 못 찾으면 앞부분."""
    if not body:
        return ""
    lowered = body.lower()
    at = min((pos for pos in (lowered.find(k.lower()) for k in keywords) if pos >= 0), default=-1)
    if at < 0:
        return body[:SNIPPET_WIDTH].strip() + ("…" if len(body) > SNIPPET_WIDTH else "")
    start = max(0, at - SNIPPET_WIDTH // 3)
    end = min(len(body), start + SNIPPET_WIDTH)
    return ("…" if start > 0 else "") + body[start:end].strip() + ("…" if end < len(body) else "")


__all__ = [
    "DEFAULT_LIMIT",
    "KIND_PERMISSIONS",
    "MAX_LIMIT",
    "SearchHit",
    "SearchResults",
    "SearchService",
]
