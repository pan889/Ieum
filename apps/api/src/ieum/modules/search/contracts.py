"""search 의 공개 인터페이스.

issues·wiki 가 자기 것을 저장할 때 여기로 색인을 넘긴다. **같은 트랜잭션**
이므로 방금 저장한 것이 곧바로 검색된다 (ADR-0005).

거꾸로 search 는 다른 모듈을 부르지 않는다. 권한 판단에 필요한 것을 색인이
직접 들고 있기 때문이다(스코프와 `restricted_to`). 부르게 두면 두 모듈이
서로를 가리키는 고리가 생긴다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.modules.search import mirror
from ieum.modules.search.backends import backend_for
from ieum.modules.search.repository import SearchRepository

ISSUE = "issue"
PAGE = "page"

#: 설정을 어디서 얻는가. 기본값은 프로세스 전역이다.
#:
#: 갈아끼울 수 있게 둔 이유는 `wiki.rooms.SessionSource` 와 같다: 미러 큐에
#: 넣는 갈래는 **설정에 따라 갈리고**, 전역을 코드 안에서 직접 부르면 시험이
#: 그 갈래를 밟을 수 없다. 밟을 수 없는 갈래는 결국 안 밟힌 채 배포된다 —
#: 그리고 이 갈래가 안 돌면 OpenSearch 색인이 조용히 비어 있는다.
settings_source: Callable[[], Settings] = get_settings


async def index_document(
    session: AsyncSession,
    *,
    kind: str,
    entity_id: UUID,
    scope_kind: str,
    scope_id: UUID,
    ref: str,
    title: str,
    body: str,
    restricted_to: Sequence[UUID] | None,
    updated_at: datetime,
) -> None:
    """색인 한 줄을 넣거나 갱신한다.

    `restricted_to` 는 객체 수준 제한을 통과할 수 있는 주체다. None 이면
    제한 없음. 스코프만으로 거르면 보안 레벨 이슈와 제한된 문서가 샌다.
    """
    await SearchRepository(session).upsert(
        {
            # id 는 컬럼 기본값(UUIDv7)이 채운다. 여기서 넣지 않는다.
            "kind": kind,
            "entity_id": entity_id,
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "ref": ref[:500],
            "title": title,
            "body": body,
            "restricted_to": list(restricted_to) if restricted_to is not None else None,
            "source_updated_at": updated_at,
        }
    )
    await _touch(session, kind, [entity_id])


async def remove_document(session: AsyncSession, *, kind: str, entity_id: UUID) -> None:
    await SearchRepository(session).remove(kind, entity_id)
    await _touch(session, kind, [entity_id])


async def remove_documents(session: AsyncSession, *, kind: str, entity_ids: Sequence[UUID]) -> None:
    await SearchRepository(session).remove_many(kind, entity_ids)
    await _touch(session, kind, entity_ids)


async def _touch(session: AsyncSession, kind: str, entity_ids: Sequence[UUID]) -> None:
    """미러를 쓰는 설치에서만 큐에 넣는다.

    설정을 여기서 읽는 이유: 이 함수들은 모듈 경계의 계약이고, 부르는 쪽
    (issues·wiki·desk)이 검색 백엔드가 무엇인지 알 이유가 없다. 인자로
    받으면 그 지식이 다섯 모듈로 퍼진다.

    **백엔드를 켠 순간부터** 큐가 쌓인다. 켜기 전에 있던 것은 큐에 없으므로
    `ieum reindex` 로 한 번 채워야 한다 — 그 말을 운영 문서에 적어 뒀다.
    """
    if settings_source().search_backend != "opensearch":
        return
    await mirror.enqueue(session, kind=kind, entity_ids=entity_ids)


__all__ = [
    "ISSUE",
    "PAGE",
    "index_document",
    "remove_document",
    "remove_documents",
    "settings_source",
]


@dataclass(frozen=True, slots=True)
class Article:
    """고객에게 추천할 문서 한 편 (desk C8).

    본문 전체를 주지 않는다. 고객 화면은 제목과 한 줄 발췌만 보여 주고,
    누르면 문서로 간다 — 발췌를 길게 주면 그것만으로 읽히고, 제한이 없는
    문서라도 통째로 퍼 나르기 좋게 만들 이유가 없다.
    """

    page_id: UUID
    #: `SPACE/slug` 모양. 링크를 만드는 데 쓴다.
    ref: str
    title: str
    excerpt: str


#: 발췌 길이. 한 줄이면 충분하고, 길면 그것만 읽고 만다.
EXCERPT = 160


async def suggest_articles(
    session: AsyncSession, *, space_id: UUID, query: str, limit: int = 5
) -> list[Article]:
    """스페이스 하나에서 **제한 없는 공개 문서**만 추천한다.

    `desk` 의 포털이 부른다. 스페이스가 고객에게 보여도 되는 것인지는 부르는
    쪽이 판단한다(`kind = "kb"` 만 걸 수 있게 한다) — 여기서 그것까지 보면
    search 가 wiki 를 알게 되고, 그 방향의 의존은 이 모듈의 규약에 어긋난다.
    """
    text = query.strip()
    if not text:
        # 빈 질의에 전부 내주지 않는다. 고객이 아직 아무 것도 안 적었는데
        # 문서 목록이 뜨면, 그건 추천이 아니라 스페이스 공개다.
        return []
    backend = backend_for(session)
    try:
        rows = await backend.public_pages_in_space(space_id=space_id, query=text, limit=limit)
    finally:
        await backend.aclose()
    return [
        Article(
            page_id=row.entity_id,
            ref=row.ref,
            title=row.title,
            excerpt=row.body[:EXCERPT].strip(),
        )
        for row in rows
    ]
