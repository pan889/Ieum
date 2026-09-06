"""검색 결과 CSV 내보내기.

검색은 1000건에서 자른다(MAX_RESULTS). 내보내기는 "전부 달라" 는 요청이라
같은 상한을 쓸 수 없으므로 **별도 경로**다 (query-language.md 3절).
대신 커서로 페이지를 넘기며 스트리밍한다 — 전부 메모리에 올리면 큰 프로젝트
하나가 워커를 죽인다.

ACL 은 SearchService 가 붙인다. 여기서 다시 거르지 않는다 — 두 곳에서 거르면
한 곳이 바뀔 때 다른 곳이 뒤처진다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ValidationError
from ieum.core.logging import get_logger
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService
from ieum.modules.identity import contracts as identity
from ieum.modules.issues.models import Issue
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService

log = get_logger(__name__)

#: 한 번에 읽어 오는 페이지 크기. 커서 페이지네이션 상한과 같다.
PAGE_SIZE = 100

#: 내보내기 전체 상한. 무제한이면 사용자가 자기 서버를 멈춘다.
MAX_ROWS = 50_000

COLUMNS = (
    "key",
    "summary",
    "type",
    "status",
    "status_category",
    "priority",
    "assignee",
    "reporter",
    "labels",
    "estimate_minutes",
    "progress",
    "start_date",
    "due_date",
    "resolved_at",
    "created_at",
    "updated_at",
)


async def stream_csv(
    session: AsyncSession,
    permissions: PermissionService,
    actor: Actor,
    iql: str,
) -> AsyncIterator[bytes]:
    """IQL 결과를 CSV 로 흘려보낸다.

    첫 청크에 BOM 을 넣는다. 없으면 엑셀이 UTF-8 을 로컬 인코딩으로 읽어
    한국어가 통째로 깨진다 — 사용자는 "내보내기가 고장났다" 고 본다.
    """
    search = SearchService(session, permissions)
    issues = IssueService(session, permissions)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    yield b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")

    names: dict[UUID, str] = {}
    cursor: str | None = None
    emitted = 0

    while True:
        page = await search.search(actor, iql, PageRequest(limit=PAGE_SIZE, cursor=cursor))
        if not page.items:
            break

        rows = await issues.to_summaries(page.items)
        await _fill_names(session, names, page.items)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for row in rows:
            issue = row.issue
            view = await issues.to_view(issue)
            writer.writerow(
                [
                    row.key,
                    issue.summary,
                    view.type_name,
                    row.state_name,
                    row.state_category,
                    issue.priority,
                    names.get(issue.assignee_id) if issue.assignee_id else "",
                    names.get(issue.reporter_id) if issue.reporter_id else "",
                    " ".join(view.labels),
                    issue.estimate_minutes if issue.estimate_minutes is not None else "",
                    issue.progress,
                    issue.start_date.isoformat() if issue.start_date else "",
                    issue.due_date.isoformat() if issue.due_date else "",
                    issue.resolved_at.isoformat() if issue.resolved_at else "",
                    issue.created_at.isoformat(),
                    issue.updated_at.isoformat(),
                ]
            )
            emitted += 1
        yield buffer.getvalue().encode("utf-8")

        if page.next_cursor is None or emitted >= MAX_ROWS:
            break
        cursor = page.next_cursor

    log.info("issues.exported", actor=str(actor.user_id), rows=emitted)


async def _fill_names(
    session: AsyncSession, names: dict[UUID, str], issues: Sequence[Issue]
) -> None:
    """담당자·보고자 이름을 채운다. 이미 아는 사람은 다시 안 읽는다."""
    wanted: set[UUID] = set()
    for issue in issues:
        for value in (issue.assignee_id, issue.reporter_id):
            if value is not None and value not in names:
                wanted.add(value)
    for user_id in wanted:
        user = await identity.get_user(session, user_id)
        names[user_id] = user.display_name if user else ""


def validate_export_query(iql: str) -> None:
    """내보내기는 무거우므로 빈 질의를 막는다.

    `iql=""` 는 "볼 수 있는 이슈 전부" 라 설치 전체를 한 파일로 뽑게 된다.
    """
    if not iql.strip():
        raise ValidationError(
            "내보낼 범위를 좁혀야 한다. 최소한 프로젝트는 지정한다.",
            code="issues.export_needs_filter",
        )


__all__ = ["COLUMNS", "MAX_ROWS", "stream_csv", "validate_export_query"]
