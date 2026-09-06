"""시간 추적. 제공하되 강제하지 않는다 (D-21).

추정(estimate_minutes)은 이슈에, 실적은 worklog 행에 쌓인다. 이슈에 합계
컬럼을 두지 않는 이유는 두 값이 어긋나는 순간(동시 입력·삭제·복구) 어느
쪽이 진실인지 알 수 없기 때문이다. 합계는 언제나 행에서 센다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from ieum.core.markdown import normalize as normalize_markdown
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import Issue, Worklog

#: 한 번에 기록할 수 있는 상한. 8주. 오타(분 단위로 480000)를 잡는다.
MAX_SPENT_MINUTES = 60 * 24 * 7 * 8


@dataclass(frozen=True, slots=True)
class TimeSummary:
    """이슈 한 건의 추정 대비 실적."""

    estimate_minutes: int | None
    spent_minutes: int
    #: 추정이 없으면 None. 음수면 초과다.
    remaining_minutes: int | None

    @property
    def over_estimate(self) -> bool:
        return self.remaining_minutes is not None and self.remaining_minutes < 0


def spent_subquery() -> object:
    """이슈별 실적 합계. IQL 의 timespent 필드가 이걸 쓴다."""
    return (
        select(func.coalesce(func.sum(Worklog.spent_minutes), 0))
        .where(Worklog.issue_id == Issue.id)
        .correlate(Issue)
        .scalar_subquery()
    )


class WorklogService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def add(
        self,
        actor: Actor,
        issue_id: UUID,
        *,
        spent_minutes: int,
        work_date: date | None = None,
        comment: str | None = None,
    ) -> Worklog:
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.WORKLOG_ADD,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        row = Worklog(
            issue_id=issue_id,
            user_id=actor.user_id,
            spent_minutes=_validate_spent(spent_minutes),
            work_date=_validate_date(work_date),
            comment=_normalized_comment(comment),
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def list_for(self, actor: Actor, issue_id: UUID) -> list[Worklog]:
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        stmt = (
            select(Worklog)
            .where(Worklog.issue_id == issue_id)
            .order_by(Worklog.work_date.desc(), Worklog.created_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def summary(self, actor: Actor, issue_id: UUID) -> TimeSummary:
        issue = await self._require_issue(issue_id)
        await self._perms.require(
            self._s,
            actor,
            perms.ISSUE_VIEW,
            scope=Scope.project(issue.project_id),
            subject=issue,
        )
        spent = (
            await self._s.execute(
                select(func.coalesce(func.sum(Worklog.spent_minutes), 0)).where(
                    Worklog.issue_id == issue_id
                )
            )
        ).scalar_one()
        estimate = issue.estimate_minutes
        return TimeSummary(
            estimate_minutes=estimate,
            spent_minutes=int(spent),
            remaining_minutes=None if estimate is None else estimate - int(spent),
        )

    async def update(
        self,
        actor: Actor,
        worklog_id: UUID,
        *,
        spent_minutes: int | None = None,
        work_date: date | None = None,
        comment: str | None = None,
        clear_comment: bool = False,
    ) -> Worklog:
        row, issue = await self._require_editable(actor, worklog_id)
        if spent_minutes is not None:
            row.spent_minutes = _validate_spent(spent_minutes)
        if work_date is not None:
            row.work_date = _validate_date(work_date)
        if clear_comment:
            row.comment = None
        elif comment is not None:
            row.comment = _normalized_comment(comment)
        await self._s.flush()
        _ = issue
        return row

    async def delete(self, actor: Actor, worklog_id: UUID) -> None:
        row, _ = await self._require_editable(actor, worklog_id)
        await self._s.delete(row)

    # ── 내부 ────────────────────────────────────────────────────

    async def _require_issue(self, issue_id: UUID) -> Issue:
        issue = await self._s.get(Issue, issue_id)
        if issue is None:
            raise NotFoundError("이슈를 찾을 수 없다.")
        return issue

    async def _require_editable(self, actor: Actor, worklog_id: UUID) -> tuple[Worklog, Issue]:
        """남의 기록을 고치려면 WORKLOG_EDIT_ANY 가 필요하다.

        자기 기록은 WORKLOG_ADD 만 있으면 고칠 수 있다 — 오타를 고치는 데
        별도 권한을 요구하면 아무도 시간을 안 적는다.
        """
        row = await self._s.get(Worklog, worklog_id)
        if row is None:
            raise NotFoundError("작업 로그를 찾을 수 없다.")
        issue = await self._require_issue(row.issue_id)
        scope = Scope.project(issue.project_id)

        if row.user_id == actor.user_id:
            await self._perms.require(self._s, actor, perms.WORKLOG_ADD, scope=scope, subject=issue)
            return row, issue

        if not await self._perms.has(
            self._s, actor, perms.WORKLOG_EDIT_ANY, scope=scope, subject=issue
        ):
            raise PermissionDeniedError(
                "타인의 작업 로그를 수정할 권한이 없다.",
                details={"permission": perms.WORKLOG_EDIT_ANY},
            )
        return row, issue


def _validate_spent(minutes: int) -> int:
    # bool 은 int 의 서브클래스다. True 가 1분이 되면 안 된다.
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValidationError("작업 시간은 정수(분)여야 한다.", code="issues.invalid_worklog")
    if minutes <= 0:
        raise ValidationError("작업 시간은 0보다 커야 한다.", code="issues.invalid_worklog")
    if minutes > MAX_SPENT_MINUTES:
        raise ValidationError(
            "한 번에 기록할 수 있는 시간을 넘었다.",
            code="issues.worklog_too_long",
            details={"max": MAX_SPENT_MINUTES},
        )
    return minutes


def _validate_date(value: date | None) -> date:
    """미래 날짜는 막는다. 아직 하지 않은 일을 기록할 수는 없다."""
    today = utcnow().date()
    if value is None:
        return today
    if value > today:
        raise ValidationError(
            "미래 날짜에는 작업을 기록할 수 없다.",
            code="issues.worklog_future_date",
            details={"today": today.isoformat()},
        )
    return value


def _normalized_comment(text: str | None) -> str | None:
    if text is None:
        return None
    return normalize_markdown(text) or None


__all__ = [
    "MAX_SPENT_MINUTES",
    "TimeSummary",
    "WorklogService",
    "spent_subquery",
]
