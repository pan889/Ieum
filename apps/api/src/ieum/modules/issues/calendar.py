"""달력 (A18, M5).

스프린트가 기간을 1급으로 만들었으니, 그 기간을 시간축이 아니라 **격자** 위에
놓는다. 번다운이 "얼마나 남았나" 라면 달력은 "언제인가" 다.

## 날짜가 없는 이슈는 놓을 수 없다

그리고 그게 대부분이다. 조용히 빼면 사람은 **거짓 그림을 보고 계획한다** —
"이번 주는 비어 있다" 고 읽는데 실제로는 날짜만 안 적힌 일이 스무 건 있다.
그래서 몇 건이 빠졌는지 세어 응답에 담는다(`undated`).

## 시작만 있는 이슈는 하루로 둔다

`start_date` 만 있으면 언제 끝나는지 **모른다.** 오늘까지 늘리면 매일 길어지는
띠가 되고, 끝없이 늘리면 달력이 그 하나로 덮인다. 모르는 것은 모르는 대로
시작한 날에 점으로 둔다.

## 스프린트 창은 시각 그대로 준다

`sprint.starts_at`/`ends_at` 은 `timestamptz` 다. 서버에서 날짜로 접으려면
타임존을 골라야 하고, 그 선택은 보는 사람마다 다르다 — 화면이 접는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import NotFoundError, ValidationError
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql.parser import parse
from ieum.modules.issues.models import Issue, Sprint
from ieum.modules.issues.search import SearchService
from ieum.modules.issues.service import IssueService
from ieum.modules.org import contracts as org

#: 한 번에 볼 수 있는 날 수. 달 하나(31일)에 앞뒤 주를 채우면 42일이고,
#: 두 달을 나란히 보는 화면까지 여유를 둔다. 이보다 넓으면 훑는 양이 화면에
#: 그릴 수 있는 양을 넘어선다.
MAX_DAYS = 70

#: 창 안에 실을 수 있는 이슈 수. 넘으면 잘린 것을 응답이 말한다.
MAX_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """달력 한 칸(또는 여러 칸)에 놓이는 이슈."""

    id: UUID
    key: str
    summary: str
    #: 놓이는 첫 날과 마지막 날. **둘 다 있다** — 없으면 놓을 수 없으므로
    #: 애초에 `entries` 에 들어오지 않는다.
    starts_on: date
    ends_on: date
    state_name: str
    state_category: str
    assignee_id: UUID | None
    priority: int
    #: 끝나기로 한 날이 지났고 아직 done 이 아니다. 오늘 기준이다.
    overdue: bool


@dataclass(frozen=True, slots=True)
class CalendarSprint:
    """달력 위에 얹는 스프린트 띠."""

    id: UUID
    name: str
    state: str
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(slots=True)
class CalendarView:
    """창 하나에 보이는 것 전부 + **안 보이는 것이 몇 건인지.**"""

    starts_on: date
    ends_on: date
    entries: list[CalendarEntry] = field(default_factory=list)
    sprints: list[CalendarSprint] = field(default_factory=list)
    #: 질의에는 맞지만 **날짜가 없어서 놓을 수 없는** 이슈 수.
    #:
    #: 조용히 빼면 사람은 거짓 그림을 보고 계획한다. 이 수가 0 이 아니면
    #: 화면은 그것을 적어야 한다.
    undated: int = 0
    #: `MAX_ENTRIES` 에서 잘렸나. 잘린 달력을 다 그린 달력으로 읽으면 없는
    #: 여유를 있다고 계획한다.
    truncated: bool = False


class CalendarService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def window(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        starts_on: date,
        ends_on: date,
        iql: str | None = None,
        today: date | None = None,
    ) -> CalendarView:
        """이 창에 걸리는 것들.

        범위는 **프로젝트 + 창**이고, `iql` 은 그 안에서 더 좁히는 것이다
        (`assignee = currentUser()` 처럼). 보드와 같은 방식으로 IQL 을 AND 로
        묶는다 — 달력 전용 필터 포맷을 만들면 워크플로우가 바뀔 때 고칠 곳이
        하나 더 는다(D-44).
        """
        await self._perms.require(self._s, actor, perms.ISSUE_VIEW, scope=Scope.project(project_id))
        _check_window(starts_on, ends_on)
        scope_iql = await self._scope_iql(project_id)
        if iql:
            # 저장 시점이 아니라 여기서 검증한다 — 창마다 오는 임시 조건이다.
            parse(iql)
            scope_iql = f"{scope_iql} AND ({iql})"

        search = SearchService(self._s, self._perms)
        rows, truncated = await search.scan(
            actor, scope_iql, limit=MAX_ENTRIES, extra_where=_overlaps(starts_on, ends_on)
        )
        # **놓을 수 없는 것을 센다.** 같은 질의에 날짜 조건만 뒤집어 센다.
        undated, _ = await search.scan(actor, scope_iql, limit=MAX_ENTRIES, extra_where=_undated())

        issues = IssueService(self._s, self._perms)
        now = today or _utctoday()
        entries: list[CalendarEntry] = []
        for row in rows:
            span = span_of(row.start_date, row.due_date)
            if span is None:
                # `_overlaps` 가 이미 걸렀으므로 여기 오면 안 된다. 방어적으로
                # 빠뜨리기보다 조용히 넘기지 않고 세는 쪽에 맡긴다.
                continue
            view = await issues.to_view(row)
            first, last = span
            entries.append(
                CalendarEntry(
                    id=row.id,
                    key=view.key,
                    summary=row.summary,
                    starts_on=first,
                    ends_on=last,
                    state_name=view.state_name,
                    state_category=view.state_category,
                    assignee_id=row.assignee_id,
                    priority=row.priority,
                    overdue=view.state_category != "done" and last < now,
                )
            )

        return CalendarView(
            starts_on=starts_on,
            ends_on=ends_on,
            entries=entries,
            sprints=await self._sprints(project_id, starts_on, ends_on),
            undated=len(undated),
            truncated=truncated,
        )

    async def _sprints(
        self, project_id: UUID, starts_on: date, ends_on: date
    ) -> list[CalendarSprint]:
        """창에 걸리는 스프린트. **닫힌 것도 준다** — 지난 주기의 경계가
        보이지 않으면 "그건 지난 스프린트 일이었다" 를 달력에서 읽을 수 없다.

        기간을 안 적은 스프린트는 놓을 자리가 없어 빠진다.
        """
        #: 날짜 경계는 `[starts_on 00:00, ends_on+1일 00:00)` 로 본다. UTC
        #: 기준이라 화면의 하루와 몇 시간 어긋날 수 있지만, 띠의 양 끝을
        #: 화면이 다시 접으므로 걸리는지만 넉넉하게 판단하면 된다.
        low = datetime.combine(starts_on, datetime.min.time())
        high = datetime.combine(ends_on + timedelta(days=1), datetime.min.time())
        rows = list(
            (
                await self._s.execute(
                    select(Sprint)
                    .where(
                        Sprint.project_id == project_id,
                        Sprint.starts_at.is_not(None),
                        Sprint.ends_at.is_not(None),
                        Sprint.starts_at < high,
                        Sprint.ends_at >= low,
                    )
                    .order_by(Sprint.starts_at)
                )
            )
            .scalars()
            .all()
        )
        return [
            CalendarSprint(
                id=row.id,
                name=row.name,
                state=row.state,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
            for row in rows
        ]

    async def _scope_iql(self, project_id: UUID) -> str:
        project = await org.get_project(self._s, project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        # 아카이브된 이슈는 달력에 올리지 않는다. 달력은 "앞으로 할 일" 을
        # 보는 화면이고, 치운 일이 남아 있으면 그 판단이 흐려진다.
        return f'project = "{project.key}" AND archived = false'


def span_of(start: date | None, due: date | None) -> tuple[date, date] | None:
    """놓이는 첫 날과 마지막 날. 놓을 수 없으면 `None`.

    **날짜 둘만 받는다.** 이슈 행을 받으면 순수하지 않아서 시험이 ORM 인스턴스
    를 만들어야 하고, 그러면 이 규칙 자체를 시험하기가 번거로워진다 — 규칙이
    SQL 쪽(`_overlaps`)과 어긋나는 것이 여기서 잡혀야 한다.

    시작만 있으면 **하루**다. 언제 끝나는지 모르는 것을 오늘까지 늘리면 매일
    길어지는 띠가 되고, 끝없이 늘리면 달력이 그 하나로 덮인다.

    끝이 시작보다 앞선 이슈(사람이 그렇게 적을 수 있다)는 뒤집어 놓는다 —
    빼 버리면 잘못 적힌 날짜를 고칠 기회조차 화면에 안 뜬다.
    """
    if start is None and due is None:
        return None
    if start is None:
        assert due is not None
        return (due, due)
    if due is None:
        return (start, start)
    return (start, due) if start <= due else (due, start)


def _overlaps(starts_on: date, ends_on: date) -> ColumnElement[bool]:
    """창과 겹치는 이슈. `_span_of` 와 **같은 규칙**이어야 한다.

    한쪽만 있는 이슈를 SQL 에서는 `COALESCE` 로 접는다: 시작이 없으면 마감이
    양 끝이고, 마감이 없으면 시작이 양 끝이다.
    """
    first = func.coalesce(Issue.start_date, Issue.due_date)
    last = func.coalesce(Issue.due_date, Issue.start_date)
    return and_(
        or_(Issue.start_date.is_not(None), Issue.due_date.is_not(None)),
        # 시작·마감이 뒤집힌 이슈까지 걸리게 양쪽으로 본다.
        func.least(first, last) <= ends_on,
        func.greatest(first, last) >= starts_on,
    )


def _undated() -> ColumnElement[bool]:
    """날짜가 하나도 없는 이슈. 놓을 수 없으므로 세기만 한다."""
    return and_(Issue.start_date.is_(None), Issue.due_date.is_(None))


def _utctoday() -> date:
    from ieum.core.time import utcnow

    return utcnow().date()


def _check_window(starts_on: date, ends_on: date) -> None:
    if ends_on < starts_on:
        raise ValidationError("끝나는 날이 시작보다 앞선다.", code="issues.calendar_window_invalid")
    if (ends_on - starts_on).days + 1 > MAX_DAYS:
        raise ValidationError(
            f"한 번에 볼 수 있는 기간은 {MAX_DAYS}일까지다.",
            code="issues.calendar_window_too_wide",
            details={"max_days": MAX_DAYS},
        )


__all__ = [
    "MAX_DAYS",
    "MAX_ENTRIES",
    "CalendarEntry",
    "CalendarService",
    "CalendarSprint",
    "CalendarView",
    "span_of",
]
