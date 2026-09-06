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

from ieum.config import get_settings
from ieum.core.logging import configure_logging, get_logger
from ieum.core.markdown import to_plaintext
from ieum.db.session import init_engine, session_scope
from ieum.modules.issues.models import Issue
from ieum.modules.issues.service import index_issue
from ieum.modules.org.models import Project
from ieum.modules.search import contracts as search
from ieum.modules.search.models import SearchDocument
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
    return 0


def main() -> int:
    return asyncio.run(run_reindex())


__all__ = ["BATCH", "main", "reindex_all", "run_reindex"]
