"""검색 색인 다시 만들기.

색인은 평소 원본과 같은 트랜잭션에서 갱신된다(ADR-0005). 그래도 다시 만들
일이 생긴다.

- 검색을 켜기 **전에** 쌓인 데이터
- 색인 규칙이 바뀐 경우(무엇을 본문으로 넣을지 등)
- 어딘가 갱신을 빠뜨린 것을 발견했을 때

멱등하다. 있는 행은 덮어쓰고, 원본이 사라진 행은 지운다.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings, get_settings
from ieum.core.logging import configure_logging, get_logger
from ieum.core.markdown import to_plaintext
from ieum.db.session import init_engine, session_scope
from ieum.modules.issues.models import Issue
from ieum.modules.issues.service import index_issue
from ieum.modules.org.models import Project
from ieum.modules.search import contracts as search
from ieum.modules.search.backends.opensearch import OpenSearchBackend
from ieum.modules.search.mirror import payload as mirror_payload
from ieum.modules.search.models import SearchDocument, SearchMirrorQueue
from ieum.modules.wiki.models import Page, PageVersion, Space

log = get_logger(__name__)

#: 한 번에 처리하는 행 수. 한꺼번에 다 읽으면 큰 설치에서 메모리를 다 쓴다.
BATCH = 200


async def reindex_all(session: AsyncSession) -> dict[str, int]:
    """전부 다시 색인한다. 처리한 개수를 종류별로 돌려준다."""
    # 먼저 비운다. 원본이 지워진 유령 행이 남으면 열 수 없는 결과가 뜬다.
    await session.execute(delete(SearchDocument))
    counts = {"issue": await _reindex_issues(session), "page": await _reindex_pages(session)}
    log.info("search.reindexed", **counts)
    return counts


async def mirror_all(session: AsyncSession, settings: Settings) -> int:
    """Postgres 색인을 **빈 OpenSearch 색인에 새로 담고 별칭을 옮긴다** (ADR-0015).

    지우고 다시 넣지 않는다. 그러면 담는 동안 검색이 0건이 되고, 되색인은
    하필 무언가 잘못됐을 때 하는 일이다. 새 색인에 다 담은 뒤 별칭을 한 번에
    옮기면, 그 순간까지 사람은 옛 색인을 보고 그 뒤로는 새 것을 본다.

    분석기를 바꿨을 때도 이 길이다 — 분석기는 색인 시점에 적용되므로 기존
    색인을 고칠 수 없고, 새로 담는 것이 유일한 방법이다.

    미러 큐도 비운다. 방금 전부 담았으므로 대기 중이던 키는 이미 반영됐고,
    남겨 두면 워커가 같은 것을 한 번 더 보낸다.
    """
    backend = OpenSearchBackend(settings)
    sent = 0
    try:
        fresh = await backend.rebuild()
        offset = 0
        while True:
            rows = list(
                (
                    await session.execute(
                        select(SearchDocument)
                        .order_by(SearchDocument.id)
                        .limit(BATCH)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            await backend.put_into(fresh, [mirror_payload(row) for row in rows])
            sent += len(rows)
            offset += BATCH
        await backend.swap(fresh)
    finally:
        await backend.aclose()
    await session.execute(delete(SearchMirrorQueue))
    log.info("search.mirrored", documents=sent, index=fresh)
    return sent


async def _reindex_issues(session: AsyncSession) -> int:
    keys = {p.id: p.key for p in (await session.execute(select(Project))).scalars().all()}
    done = 0
    offset = 0
    while True:
        rows = list(
            (
                await session.execute(
                    select(Issue)
                    .where(Issue.archived_at.is_(None))
                    .order_by(Issue.id)
                    .limit(BATCH)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return done
        for issue in rows:
            await index_issue(session, issue, project_key=keys.get(issue.project_id, ""))
        done += len(rows)
        offset += BATCH


async def _reindex_pages(session: AsyncSession) -> int:
    space_keys = {s.id: s.key for s in (await session.execute(select(Space))).scalars().all()}
    done = 0
    offset = 0
    while True:
        rows = list(
            (
                await session.execute(
                    select(Page)
                    .where(Page.archived_at.is_(None), Page.status == "published")
                    .order_by(Page.path)
                    .limit(BATCH)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return done
        for page in rows:
            body = ""
            if page.current_version_id is not None:
                version = await session.get(PageVersion, page.current_version_id)
                body = to_plaintext(version.body) if version else ""
            await search.index_document(
                session,
                kind=search.PAGE,
                entity_id=page.id,
                scope_kind="space",
                scope_id=page.space_id,
                ref=f"{space_keys.get(page.space_id, '')}/{page.path}",
                title=page.title,
                body=body,
                # 제한은 서비스가 계산한다. 여기서는 보수적으로 비워 두지
                # 않는다 — 제한된 문서를 열어 버리는 쪽이 더 나쁘다.
                restricted_to=await _viewers(session, page),
                updated_at=page.updated_at,
            )
        done += len(rows)
        offset += BATCH


async def _viewers(session: AsyncSession, page: Page) -> list[UUID] | None:
    """가장 가까운 조상의 열람 제한. 서비스의 `_effective_viewers` 와 같은 규칙."""
    from ieum.modules.wiki.models import PageRestriction

    parts = page.path.split("/")
    paths = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    rows = list(
        (
            await session.execute(
                select(Page.id, Page.path)
                .where(Page.space_id == page.space_id, Page.path.in_(paths))
                .order_by(Page.path)
            )
        ).all()
    )
    by_path = {path: page_id for page_id, path in rows}
    for path in reversed(paths):
        page_id = by_path.get(path)
        if page_id is None:
            continue
        rules = list(
            (
                await session.execute(
                    select(PageRestriction.principal_id).where(
                        PageRestriction.page_id == page_id, PageRestriction.mode == "view"
                    )
                )
            )
            .scalars()
            .all()
        )
        if rules:
            return rules
    return None


async def run_reindex() -> int:
    settings = get_settings()
    configure_logging(debug=settings.debug, json_output=False)
    init_engine(settings)
    async with session_scope() as session:
        counts = await reindex_all(session)
    print(f"색인 완료: 이슈 {counts['issue']}건, 문서 {counts['page']}건")
    if settings.search_backend == "opensearch":
        # **세션을 새로 뜬다.** 위의 `session` 은 블록을 나오며 닫혔고, 닫힌
        # 세션을 쓰면 "greenlet is being finalized" 로 죽는다 — 원인이
        # 검색과 아무 상관 없어 보이는 말이다(실제로 그렇게 죽었다).
        #
        # 그리고 **커밋된 뒤에** 비춘다. 같은 트랜잭션에서 읽어 보내면 그
        # 커밋이 실패했을 때 사본에만 있는 문서가 생긴다.
        async with session_scope() as fresh:
            sent = await mirror_all(fresh, settings)
        print(f"OpenSearch 로 옮김: {sent}건")
    return 0


def main() -> int:
    return asyncio.run(run_reindex())


__all__ = ["BATCH", "main", "mirror_all", "reindex_all", "run_reindex"]
