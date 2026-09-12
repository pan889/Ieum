"""OpenSearch 백엔드 — 큰 설치용 (ADR-0015).

## 왜 클라이언트 라이브러리를 안 쓰는가

`opensearch-py` 를 넣지 않고 `httpx` 로 직접 부른다. 쓰는 것이 다섯 개
(색인 만들기, bulk, delete, search, 별칭 바꾸기)뿐이고, 그 다섯 개의 **몸통이
코드에 그대로 보이는 것**이 이 파일에서는 값이다 — 권한 필터가 질의 안에
있고, 그것이 맞는지는 실제로 보내는 JSON 을 읽어야 판단할 수 있다.

## 질의 문법을 맞춘다

PGroonga 의 `&@~` 와 OpenSearch 의 질의 언어는 다르다. 같은 검색창이 백엔드에
따라 다르게 동작하면, 그건 "검색이 이상하다" 로만 보고된다. 그래서
`translate_query` 로 옮기고, 두 백엔드에 **같은 질의를 던지는 시험**이 그
같음을 붙잡는다.

`simple_query_string` 을 쓴다. 이유는 하나뿐이다: **문법이 깨져도 예외를
내지 않는다.** `query_string` 은 400 을 내는데, 사람이 검색창에 아무거나 치는
자리에서 그건 고장이다. PGroonga 도 깨진 질의에 0건을 낸다.

## 한국어

기본 분석기는 `cjk` 다. 루씬에 들어 있어 **플러그인 없이** 돌지만, 한국어를
두 글자씩 쪼갠다(`문서로` → `문서`·`서로`). 되찾기는 되고 헛맞음이 생긴다.
`analysis-nori` 를 설치한 설치라면 `IEUM_OPENSEARCH_ANALYZER=nori` 로 바꾼다.
바꾸면 **되색인이 필요하다** — 분석기는 색인 시점에 적용된다.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from ieum.config import Settings
from ieum.core.logging import get_logger
from ieum.core.permissions import Acl
from ieum.modules.search.backends.base import IndexedDocument

log = get_logger(__name__)


#: 한 문서의 `_id`. 같은 원본을 두 번 넣지 않기 위해 우리가 정한다 —
#: OpenSearch 가 지어 주게 두면 갱신이 새 문서가 된다.
def document_id(kind: str, entity_id: UUID) -> str:
    return f"{kind}:{entity_id}"


#: 질의 낱말. 공백과 인용부호, 괄호로 끊는다.
_TOKEN = re.compile(r'"[^"]*"|\S+')
#: `simple_query_string` 에게 허용하는 문법. PGroonga 가 못 하는 것(접두사
#: 검색, 퍼지, 근접)은 **일부러 뺀다** — 한쪽에만 있는 기능은 백엔드를 바꾼
#: 날 검색 결과가 달라지는 이유가 된다.
FLAGS = "AND|OR|NOT|PHRASE|PRECEDENCE|WHITESPACE"


def translate_query(query: str) -> str:
    """PGroonga 질의를 `simple_query_string` 으로 옮긴다.

    - `OR` (낱말 하나로 선 것) → `|`
    - `AND` → 지운다. 기본 연산자가 AND 이므로 있으나 없으나 같다.
    - `-낱말`, `"구절"`, 괄호 → 그대로. 양쪽이 같은 뜻으로 읽는다.

    낱말 안에 든 `or` 는 건드리지 않는다 — `for` 를 `f|` 로 만들면 안 된다.
    """
    out: list[str] = []
    for word in _TOKEN.findall(query):
        if word == "OR":
            out.append("|")
        elif word == "AND":
            continue
        else:
            out.append(word)
    return " ".join(out)


def mapping(analyzer: str) -> dict[str, Any]:
    """색인 스키마. `search_document` 의 열과 하나씩 맞춘다.

    `title`·`body` 만 분석하고 나머지는 `keyword` 다. 권한 필터가 정확히
    같아야 하는 값들이라(스코프 id, 주체 id) 분석기가 손대면 안 된다 —
    쪼개진 UUID 는 남의 것과도 맞을 수 있다.
    """
    return {
        "mappings": {
            # 색인에 없는 열이 질의에 들어가면 **조용히 0건**이 된다.
            # 그것보다 400 이 낫다.
            "dynamic": "strict",
            "properties": {
                "kind": {"type": "keyword"},
                "entity_id": {"type": "keyword"},
                "scope_kind": {"type": "keyword"},
                "scope_id": {"type": "keyword"},
                "ref": {"type": "keyword"},
                "title": {"type": "text", "analyzer": analyzer},
                "body": {"type": "text", "analyzer": analyzer},
                "restricted_to": {"type": "keyword"},
                "source_updated_at": {"type": "date"},
            },
        },
        "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
    }


class OpenSearchBackend:
    """읽기와 미러 쓰기. 둘을 한 클래스에 두는 이유는 색인 이름과 붙는 것이
    같기 때문이다 — 나누면 주소를 두 곳에서 읽는다."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._base = (settings.opensearch_url or "").rstrip("/")
        self._alias = settings.opensearch_index
        self._analyzer = settings.opensearch_analyzer
        auth: tuple[str, str] | None = None
        if settings.opensearch_username:
            auth = (settings.opensearch_username, settings.opensearch_password.get_secret_value())
        self._client = client or httpx.AsyncClient(
            base_url=self._base,
            # 검색은 사람이 기다리는 자리다. 오래 기다리는 것보다 실패가 낫다.
            timeout=httpx.Timeout(10.0, connect=3.0),
            auth=auth,
        )

    # ── 읽기 ────────────────────────────────────────────────────

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
        scope = _scope_filter(acls, kinds)
        if scope is None:
            return [], 0

        body = {
            "from": offset,
            "size": limit,
            "track_total_hits": True,
            "query": {
                "bool": {
                    "must": [_matched(query, self._analyzer)],
                    "filter": [scope, _restriction(principal_ids)],
                }
            },
            # 점수가 같으면 최근 것을 위로. PGroonga 쪽과 같은 규칙이다.
            "sort": [{"_score": "desc"}, {"source_updated_at": "desc"}],
        }
        found = await self._post(f"/{self._alias}/_search", body)
        hits = found.get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        return [_document(h["_source"]) for h in hits.get("hits", [])], int(total)

    async def public_pages_in_space(
        self, *, space_id: UUID, query: str, limit: int
    ) -> list[IndexedDocument]:
        body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [_matched(query, self._analyzer)],
                    "filter": [
                        {"term": {"kind": "page"}},
                        {"term": {"scope_kind": "space"}},
                        {"term": {"scope_id": str(space_id)}},
                    ],
                    # 제한이 **하나라도** 걸린 문서는 뺀다. 고객에게는
                    # "제한이 없는 것" 만 보여 준다 — Postgres 쪽의
                    # `restricted_to IS NULL` 과 같은 뜻이다.
                    "must_not": [{"exists": {"field": "restricted_to"}}],
                }
            },
            "sort": [{"_score": "desc"}, {"source_updated_at": "desc"}],
        }
        found = await self._post(f"/{self._alias}/_search", body)
        return [_document(h["_source"]) for h in found.get("hits", {}).get("hits", [])]

    # ── 미러 쓰기 ───────────────────────────────────────────────

    async def put(self, documents: Sequence[dict[str, Any]]) -> None:
        """색인하거나 덮어쓴다. `_id` 를 우리가 정하므로 멱등하다."""
        if not documents:
            return
        lines: list[str] = []
        for doc in documents:
            lines.append(
                _json(
                    {
                        "index": {
                            "_index": self._alias,
                            "_id": document_id(doc["kind"], doc["entity_id"]),
                        }
                    }
                )
            )
            lines.append(_json(_source(doc)))
        await self._bulk(lines)

    async def drop(self, keys: Sequence[tuple[str, UUID]]) -> None:
        """지운다. 없는 것을 지우는 것은 오류가 아니다 (`404` 를 무시한다)."""
        if not keys:
            return
        lines = [
            _json({"delete": {"_index": self._alias, "_id": document_id(kind, entity_id)}})
            for kind, entity_id in keys
        ]
        await self._bulk(lines)

    # ── 색인 만들기·바꿔 달기 ───────────────────────────────────

    async def ensure_index(self) -> None:
        """별칭이 없으면 색인 하나를 만들어 걸어 둔다.

        미러가 처음 돌 때 색인이 없으면 OpenSearch 가 알아서 만들어 주는데,
        그때 만들어지는 것은 **우리 매핑이 아니다** — 한국어 분석기 없이
        기본값으로 서고, 그러면 검색이 조용히 나빠진다.
        """
        if (await self._client.head(f"/_alias/{self._alias}")).status_code == 200:
            return
        name = self._new_index_name()
        await self._put(f"/{name}", mapping(self._analyzer))
        await self._post("/_aliases", {"actions": [{"add": {"index": name, "alias": self._alias}}]})
        log.info("search.opensearch.index_created", index=name, alias=self._alias)

    async def rebuild(self) -> str:
        """빈 색인을 새로 만들고 이름을 돌려준다. **별칭은 아직 안 옮긴다.**

        되색인 중에도 검색이 되게 하려는 것이다. 지우고 다시 넣으면 그동안
        검색이 0건이 되고, 되색인은 하필 무언가 잘못됐을 때 하는 일이다.
        """
        name = self._new_index_name()
        await self._put(f"/{name}", mapping(self._analyzer))
        return name

    def _new_index_name(self) -> str:
        """`{별칭}-{시각}-{임의}`.

        시각만 붙이면 **같은 초에 두 번 만들 때 부딪힌다** — 되색인을 잇달아
        돌리면 두 번째가 400 을 받는다(실제로 받았다). 시각은 사람이 읽기
        위한 것이고, 유일함은 뒤의 임의 부분이 맡는다.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"{self._alias}-{stamp}-{secrets.token_hex(3)}"

    async def put_into(self, index: str, documents: Sequence[dict[str, Any]]) -> None:
        if not documents:
            return
        lines: list[str] = []
        for doc in documents:
            lines.append(
                _json(
                    {"index": {"_index": index, "_id": document_id(doc["kind"], doc["entity_id"])}}
                )
            )
            lines.append(_json(_source(doc)))
        await self._bulk(lines)

    async def swap(self, index: str) -> None:
        """별칭을 새 색인으로 **한 번에** 옮기고 옛 것을 지운다.

        `_aliases` 한 번에 remove+add 를 담는다. 나눠 부르면 그 사이에 별칭이
        없는 순간이 생기고, 그 순간의 검색은 404 다.
        """
        current = await self._client.get(f"/_alias/{self._alias}")
        olds = list(current.json()) if current.status_code == 200 else []
        actions: list[dict[str, Any]] = [
            {"remove": {"index": name, "alias": self._alias}} for name in olds if name != index
        ]
        actions.append({"add": {"index": index, "alias": self._alias}})
        await self._post("/_aliases", {"actions": actions})
        for name in olds:
            if name != index:
                await self._client.delete(f"/{name}")
        log.info("search.opensearch.swapped", index=index, dropped=olds)

    # ── 살아 있나 ───────────────────────────────────────────────

    async def ping(self) -> None:
        """`/readyz` 가 부른다. 못 붙으면 예외를 올린다."""
        response = await self._client.get("/_cluster/health", timeout=3.0)
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 안쪽 ────────────────────────────────────────────────────

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=body)
        response.raise_for_status()
        return dict(response.json())

    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.put(path, json=body)
        response.raise_for_status()
        return dict(response.json())

    async def _bulk(self, lines: Sequence[str]) -> None:
        """`_bulk` 는 **줄 단위 JSON** 이고 마지막에 개행이 필요하다.

        200 을 받아도 안을 봐야 한다: bulk 는 한 줄이 실패해도 200 이고
        `errors: true` 로만 말한다. 안 보면 색인이 조용히 빈다.
        """
        payload = "\n".join(lines) + "\n"
        response = await self._client.post(
            "/_bulk", content=payload.encode(), headers={"content-type": "application/x-ndjson"}
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("errors"):
            return
        failed = [
            item
            for entry in result.get("items", [])
            for item in entry.values()
            # 없는 것을 지우는 것은 실패가 아니다.
            if item.get("error") and item.get("status") != 404
        ]
        if failed:
            raise RuntimeError(f"bulk 색인 실패 {len(failed)}건: {failed[0].get('error')}")


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), default=str)


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    """색인에 실을 몸통. `restricted_to` 가 **`None` 이면** 열을 아예 뺀다.

    `null` 로 넣으면 `exists` 가 그 열을 "없다" 로 보므로 뜻이 같지만,
    빼는 쪽이 매핑의 `strict` 와 어긋날 일이 없고 저장도 작다.

    **빈 목록은 빼지 않는다.** 빈 목록은 "제한 없음" 이 아니라 "아무도 못
    본다" 이고, 빼 버리면 그 뜻이 정반대로 뒤집힌다.
    """
    body = {
        "kind": doc["kind"],
        "entity_id": str(doc["entity_id"]),
        "scope_kind": doc["scope_kind"],
        "scope_id": str(doc["scope_id"]),
        "ref": doc["ref"],
        "title": doc["title"],
        "body": doc["body"],
        "source_updated_at": doc["source_updated_at"],
    }
    restricted = doc.get("restricted_to")
    # **`None` 과 빈 목록은 다르다.** `None` 은 "제한 없음", 빈 목록은
    # "아무도 못 본다" 다 — 위키의 중첩 제한이 서로 안 겹치면 그렇게 된다.
    # 둘을 같이 보면 아무도 못 볼 문서가 모두에게 보이는 쪽으로 뒤집힌다.
    if restricted is not None:
        body["restricted_to"] = [str(one) for one in restricted]
    return body


def _matched(query: str, analyzer: str) -> dict[str, Any]:
    return {
        "simple_query_string": {
            "query": translate_query(query),
            # 제목이 맞으면 더 위로. PGroonga 쪽은 인덱스 점수가 알아서
            # 제목을 더 세게 보므로, 여기서 손으로 맞춘다.
            "fields": ["title^3", "body"],
            "default_operator": "and",
            "flags": FLAGS,
            "analyzer": analyzer,
        }
    }


def _restriction(principal_ids: frozenset[UUID]) -> dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"bool": {"must_not": [{"exists": {"field": "restricted_to"}}]}},
                {"terms": {"restricted_to": sorted(str(one) for one in principal_ids)}},
            ],
            "minimum_should_match": 1,
        }
    }


def _scope_filter(acls: dict[str, Acl], kinds: Sequence[str]) -> dict[str, Any] | None:
    """`PostgresBackend._scope_filter` 와 **같은 규칙**이다.

    두 곳에 같은 규칙이 있는 것은 부담이지만, 여기서 합칠 방법은 조건을
    추상 트리로 만들어 두 방언으로 컴파일하는 것뿐이고 그건 이 규칙 하나에
    비해 크다. 대신 시험이 두 백엔드에 같은 질의를 던져 같음을 붙잡는다.
    """
    clauses: list[dict[str, Any]] = []
    for kind in kinds:
        acl = acls.get(kind)
        if acl is None or acl.is_empty:
            continue
        if acl.is_global:
            clauses.append({"term": {"kind": kind}})
            continue
        ids = acl.project_ids if kind == "issue" else acl.space_ids
        if not ids:
            continue
        clauses.append(
            {
                "bool": {
                    "filter": [
                        {"term": {"kind": kind}},
                        {"terms": {"scope_id": sorted(str(one) for one in ids)}},
                    ]
                }
            }
        )
    if not clauses:
        return None
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def _document(source: dict[str, Any]) -> IndexedDocument:
    return IndexedDocument(
        kind=source["kind"],
        entity_id=UUID(source["entity_id"]),
        scope_id=UUID(source["scope_id"]),
        ref=source["ref"],
        title=source["title"],
        body=source["body"],
        source_updated_at=datetime.fromisoformat(source["source_updated_at"]),
    )


__all__ = ["FLAGS", "OpenSearchBackend", "document_id", "mapping", "translate_query"]
