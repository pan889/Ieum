"""검색 백엔드의 계약 (ADR-0005, ADR-0015).

## 무엇이 갈리고 무엇이 안 갈리는가

**쓰기는 갈리지 않는다.** 색인은 언제나 Postgres 의 `search_document` 에
원본과 같은 트랜잭션으로 쓴다. OpenSearch 는 그 표를 비추는 **읽기 쪽**이다.
그렇게 둔 이유가 셋이다.

1. OpenSearch 가 죽어도 이슈 저장이 실패하지 않는다. 색인 때문에 본업이
   실패하는 것은 있을 수 없다.
2. 되색인이 원본을 다시 훑지 않는다 — `search_document` 가 이미 OpenSearch 가
   원하는 평평한 모양이다.
3. 백엔드를 되돌릴 때 아무것도 안 해도 된다. Postgres 색인은 계속 최신이다.

**읽기는 갈린다.** 그래서 이 파일이 있다. 두 구현이 같은 질문에 같은 답을
내야 하고, 그 "같음" 을 시험이 두 백엔드에 **같은 질의를 던져** 확인한다
(`test_search_backends.py`).

## 권한은 질의에 얹는다

가져와서 거르면 페이지 크기가 어긋나고, 한 군데만 빠뜨리면 그게 유출이다.
그래서 두 백엔드 모두 스코프와 `restricted_to` 를 **질의 안에서** 거른다.
이 계약이 `acls` 와 `principal_ids` 를 받는 이유이고, 백엔드가 그것을 무시할
수 없게 하려고 결과에 더 이상 거를 것을 남기지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ieum.core.permissions import Acl


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """색인 한 줄. **ORM 행이 아니다.**

    백엔드가 ORM 행을 내주면 서비스가 Postgres 를 알게 되고, 그러면
    OpenSearch 구현이 가짜 ORM 객체를 만들어야 한다. 평평한 값으로 끊는다.
    """

    kind: str
    entity_id: UUID
    scope_id: UUID
    ref: str
    title: str
    body: str
    source_updated_at: datetime


class SearchBackend(Protocol):
    """읽기 쪽. 쓰기는 `search.contracts` 가 Postgres 에 직접 한다."""

    async def search(
        self,
        *,
        query: str,
        acls: dict[str, Acl],
        principal_ids: frozenset[UUID],
        kinds: Sequence[str],
        limit: int,
        offset: int,
    ) -> tuple[list[IndexedDocument], int]:
        """맞은 것과 **총 개수**. 개수는 페이지 매김에 쓰므로 함께 준다."""
        ...

    async def public_pages_in_space(
        self, *, space_id: UUID, query: str, limit: int
    ) -> list[IndexedDocument]:
        """제한이 아예 없는 문서만 (desk C8). 고객에게 보여 줄 것이다."""
        ...

    async def aclose(self) -> None:
        """붙어 있는 것을 놓는다. Postgres 쪽은 할 일이 없다."""
        ...


__all__ = ["IndexedDocument", "SearchBackend"]
