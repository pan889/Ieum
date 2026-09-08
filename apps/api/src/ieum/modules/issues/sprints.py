"""스프린트와 번다운 (M5).

## 스프린트가 라벨이 아닌 이유

라벨로 두면 "언제부터 언제까지" 를 담을 곳이 없다. 번다운은 남은 일을
**시간축**에 놓는 그림이므로, 시간이 먼저 있어야 한다.

## 닫을 때 남은 것을 잃지 않는다

끝난 스프린트에 안 끝난 이슈가 남아 있는 것은 정상이다. 그것을 어디로
보낼지 **부르는 쪽이 정한다**: 다음 스프린트이거나 백로그다. 기본값을 두지
않는 이유는, 조용히 백로그로 흘려보내면 "다음에 하기로 했던 것" 이 아무도
안 보는 곳으로 사라지기 때문이다.

## 번다운은 되짚어 계산하지 않는다

지금 상태로 과거를 그리면 어제 추가된 이슈가 첫날부터 있었던 것이 되고,
**범위가 늘어난 사실이 그림에서 사라진다.** 그날 값을 그날 적는다
(`snapshot_sprints`, 워커가 부른다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import (
    Issue,
    Sprint,
    SprintSnapshot,
    WorkflowState,
)

log = get_logger(__name__)

MAX_NAME = 200

#: 목록 순서: 도는 것 → 예정 → 끝난 것.
#:
#: **`state` 로 그냥 정렬하면 안 된다.** 알파벳순은 active, closed, future 라
#: 끝난 스프린트가 예정된 것 앞에 온다 — 계획 회의에서 보는 화면인데 지난 것이
#: 다음 것을 가린다.
_STATE_ORDER = case(
    (Sprint.state == "active", 0),
    (Sprint.state == "future", 1),
    else_=2,
)


@dataclass(frozen=True, slots=True)
class SprintTotals:
    """스프린트 하나에 지금 들어 있는 양."""

    issues: int
    minutes: int
    remaining_issues: int
    remaining_minutes: int


@dataclass(frozen=True, slots=True)
class SprintView:
    sprint: Sprint
    totals: SprintTotals


@dataclass(frozen=True, slots=True)
class BurndownPoint:
    on_date: date
    remaining_issues: int
    remaining_minutes: int
    total_issues: int
    total_minutes: int


class SprintService:
    """스프린트 정의와 이슈 배치."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    # ── 읽기 ────────────────────────────────────────────────────

    async def list_for(self, actor: Actor, project_id: UUID) -> list[SprintView]:
        """이 프로젝트의 스프린트 전부. **닫힌 것도 준다** — 지난 번다운을
        열어 볼 수 없으면 기록을 남기는 뜻이 없다."""
        await self._require(actor, project_id, perms.ISSUE_VIEW)
        rows = list(
            (
                await self._s.execute(
                    select(Sprint)
                    .where(Sprint.project_id == project_id)
                    .order_by(_STATE_ORDER, Sprint.position, Sprint.starts_at)
                )
            )
            .scalars()
            .all()
        )
        return [SprintView(sprint=row, totals=await self._totals(row.id)) for row in rows]

    async def burndown(self, actor: Actor, sprint_id: UUID) -> list[BurndownPoint]:
        """찍힌 점들. **없으면 빈 목록이다** — 아직 안 시작한 스프린트다."""
        sprint = await self._require_sprint(actor, sprint_id, perms.ISSUE_VIEW)
        rows = (
            (
                await self._s.execute(
                    select(SprintSnapshot)
                    .where(SprintSnapshot.sprint_id == sprint.id)
                    .order_by(SprintSnapshot.on_date)
                )
            )
            .scalars()
            .all()
        )
        return [
            BurndownPoint(
                on_date=row.on_date,
                remaining_issues=row.remaining_issues,
                remaining_minutes=row.remaining_minutes,
                total_issues=row.total_issues,
                total_minutes=row.total_minutes,
            )
            for row in rows
        ]

    # ── 정의 ────────────────────────────────────────────────────

    async def create(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        name: str,
        goal: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> Sprint:
        await self._require(actor, project_id, perms.SPRINT_MANAGE)
        clean = _clean_name(name)
        _check_window(starts_at, ends_at)
        if await self._name_taken(project_id, clean):
            raise ConflictError("같은 이름의 스프린트가 있다.", code="issues.sprint_name_taken")

        row = Sprint(
            project_id=project_id,
            name=clean,
            goal=(goal or "").strip() or None,
            state="future",
            starts_at=starts_at,
            ends_at=ends_at,
            position=await self._next_position(project_id),
        )
        self._s.add(row)
        await self._s.flush()
        log.info("issues.sprint.created", sprint=str(row.id), project=str(project_id))
        return row

    async def update(
        self,
        actor: Actor,
        sprint_id: UUID,
        *,
        name: str | None = None,
        goal: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        clear_goal: bool = False,
        clear_starts_at: bool = False,
        clear_ends_at: bool = False,
    ) -> Sprint:
        """고친다. `None` 은 **"안 건드린다"** 이고, 지우려면 `clear_*` 를 쓴다.

        JSON 에는 "값 없음" 과 `null` 을 가릴 방법이 없다. 둘을 같게 다루면
        기간을 지우려는 요청이 조용히 아무 일도 안 하고 성공한다 — 화면은
        지워진 줄 알고, 다음에 열면 그대로 있다. 보드(`clear_swimlane`)와 같은
        방식이다.
        """
        row = await self._require_sprint(actor, sprint_id, perms.SPRINT_MANAGE)
        if row.state == "closed":
            # 닫힌 스프린트를 고치면 그때 찍힌 번다운과 어긋난다.
            raise ConflictError("닫힌 스프린트는 고칠 수 없다.", code="issues.sprint_closed")
        if name is not None:
            clean = _clean_name(name)
            if await self._name_taken(row.project_id, clean, exclude=row.id):
                raise ConflictError("같은 이름의 스프린트가 있다.", code="issues.sprint_name_taken")
            row.name = clean
        if clear_goal:
            row.goal = None
        elif goal is not None:
            row.goal = goal.strip() or None
        if clear_starts_at:
            row.starts_at = None
        elif starts_at is not None:
            row.starts_at = starts_at
        if clear_ends_at:
            row.ends_at = None
        elif ends_at is not None:
            row.ends_at = ends_at
        _check_window(row.starts_at, row.ends_at)
        await self._s.flush()
        return row

    async def delete(self, actor: Actor, sprint_id: UUID) -> None:
        """지운다. **이슈는 백로그로 돌아간다**(`SET NULL`) — 같이 사라지지 않는다."""
        row = await self._require_sprint(actor, sprint_id, perms.SPRINT_MANAGE)
        if row.state == "active":
            raise ConflictError(
                "도는 스프린트는 지울 수 없다. 먼저 닫는다.", code="issues.sprint_active"
            )
        await self._s.delete(row)
        await self._s.flush()

    # ── 삶 ──────────────────────────────────────────────────────

    async def start(self, actor: Actor, sprint_id: UUID) -> Sprint:
        """시작한다. 여기서부터 번다운이 찍힌다."""
        row = await self._require_sprint(actor, sprint_id, perms.SPRINT_MANAGE)
        if row.state != "future":
            raise ConflictError(
                "시작할 수 있는 스프린트가 아니다.", code="issues.sprint_not_startable"
            )
        row.state = "active"
        row.activated_at = utcnow()
        try:
            await self._s.flush()
        except IntegrityError as exc:
            # **부분 유니크 인덱스가 막는다.** 두 사람이 동시에 시작하면
            # 애플리케이션 검사만으로는 둘 다 통과한다.
            raise ConflictError(
                "이미 도는 스프린트가 있다.", code="issues.sprint_already_active"
            ) from exc

        # 첫 점을 지금 찍는다. 안 찍으면 하루짜리 스프린트의 번다운이 빈다.
        await snapshot_one(self._s, row)
        log.info("issues.sprint.started", sprint=str(row.id))
        return row

    async def close(
        self, actor: Actor, sprint_id: UUID, *, move_to: UUID | None, to_backlog: bool = False
    ) -> Sprint:
        """닫는다. **남은 것을 어디로 보낼지 반드시 말해야 한다.**

        기본값을 두지 않는다: 조용히 백로그로 흘려보내면 "다음에 하기로 했던
        것" 이 아무도 안 보는 곳으로 간다. `to_backlog=True` 도 **선택한**
        것이지 넘어간 것이 아니다.
        """
        row = await self._require_sprint(actor, sprint_id, perms.SPRINT_MANAGE)
        if row.state != "active":
            raise ConflictError("도는 스프린트가 아니다.", code="issues.sprint_not_active")
        if move_to is None and not to_backlog:
            raise ValidationError(
                "남은 이슈를 어디로 보낼지 정해야 한다.", code="issues.sprint_needs_disposition"
            )
        target: Sprint | None = None
        if move_to is not None:
            if to_backlog:
                raise ValidationError(
                    "다음 스프린트와 백로그를 함께 고를 수 없다.",
                    code="issues.sprint_disposition_ambiguous",
                )
            target = await self._s.get(Sprint, move_to)
            if target is None or target.project_id != row.project_id:
                raise NotFoundError("옮길 스프린트를 찾을 수 없다.")
            if target.id == row.id:
                raise ValidationError(
                    "자기 자신으로 옮길 수 없다.", code="issues.sprint_disposition_self"
                )
            if target.state == "closed":
                raise ValidationError(
                    "닫힌 스프린트로는 옮길 수 없다.", code="issues.sprint_closed"
                )

        # **마지막 점을 먼저 찍는다.** 옮기고 나서 찍으면 남은 것이 0 이 되고,
        # 번다운은 완주한 것처럼 보인다 — 실제로는 옮긴 것이다.
        await snapshot_one(self._s, row)

        moved = await self._move_unfinished(row.id, target.id if target else None)
        row.state = "closed"
        row.closed_at = utcnow()
        await self._s.flush()
        log.info(
            "issues.sprint.closed",
            sprint=str(row.id),
            moved=moved,
            to=str(target.id) if target else "backlog",
        )
        return row

    # ── 이슈 배치 ───────────────────────────────────────────────

    async def assign_issues(
        self, actor: Actor, *, sprint_id: UUID | None, issue_ids: list[UUID], project_id: UUID
    ) -> int:
        """이슈를 스프린트에 넣거나(`sprint_id`) 백로그로 뺀다(`None`).

        **이슈 수정 권한으로 한다** — 스프린트 관리 권한이 아니다. 스프린트를
        만드는 것과 자기 일감을 이번 주기에 넣는 것은 다른 일이고, 후자는
        팀원 전부가 한다.
        """
        await self._require(actor, project_id, perms.ISSUE_EDIT)
        if not issue_ids:
            return 0
        target: Sprint | None = None
        if sprint_id is not None:
            target = await self._s.get(Sprint, sprint_id)
            if target is None or target.project_id != project_id:
                raise NotFoundError("스프린트를 찾을 수 없다.")
            if target.state == "closed":
                raise ValidationError(
                    "닫힌 스프린트에는 넣을 수 없다.", code="issues.sprint_closed"
                )

        rows = list(
            (
                await self._s.execute(
                    select(Issue).where(Issue.id.in_(issue_ids), Issue.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != len(set(issue_ids)):
            # 다른 프로젝트의 이슈가 섞였다. 부분 성공으로 두면 부르는 쪽은
            # 무엇이 안 들어갔는지 모른다.
            raise ValidationError(
                "이 프로젝트의 이슈가 아니다.", code="issues.sprint_issue_foreign"
            )
        for issue in rows:
            issue.sprint_id = target.id if target else None
        await self._s.flush()
        return len(rows)

    # ── 내부 ────────────────────────────────────────────────────

    async def _move_unfinished(self, sprint_id: UUID, target_id: UUID | None) -> int:
        """안 끝난 것을 옮긴다. **아카이브된 것은 두고 간다.**

        `totals_of` 가 아카이브를 안 세므로, 닫기 화면이 "N 건 남았다" 고 말한
        수와 실제로 옮기는 수가 어긋나면 안 된다. 그리고 아카이브된 이슈가
        있던 곳은 **닫힌 그 스프린트**다 — 되살릴 때 그 사실이 남아 있는 것이
        맞다.
        """
        rows = list(
            (
                await self._s.execute(
                    select(Issue)
                    .join(WorkflowState, WorkflowState.id == Issue.state_id)
                    .where(
                        Issue.sprint_id == sprint_id,
                        Issue.archived_at.is_(None),
                        WorkflowState.category != "done",
                    )
                )
            )
            .scalars()
            .all()
        )
        for issue in rows:
            issue.sprint_id = target_id
        await self._s.flush()
        return len(rows)

    async def _totals(self, sprint_id: UUID) -> SprintTotals:
        return await totals_of(self._s, sprint_id)

    async def _name_taken(
        self, project_id: UUID, name: str, *, exclude: UUID | None = None
    ) -> bool:
        stmt = select(Sprint.id).where(Sprint.project_id == project_id, Sprint.name == name)
        if exclude is not None:
            stmt = stmt.where(Sprint.id != exclude)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def _next_position(self, project_id: UUID) -> int:
        found = (
            await self._s.execute(
                select(func.max(Sprint.position)).where(Sprint.project_id == project_id)
            )
        ).scalar_one_or_none()
        return int(found or 0) + 1

    async def _require_sprint(self, actor: Actor, sprint_id: UUID, permission: str) -> Sprint:
        row = await self._s.get(Sprint, sprint_id)
        if row is None:
            raise NotFoundError("스프린트를 찾을 수 없다.")
        await self._require(actor, row.project_id, permission)
        return row

    async def _require(self, actor: Actor, project_id: UUID, permission: str) -> None:
        await self._perms.require(self._s, actor, permission, scope=Scope.project(project_id))


# ── 번다운 찍기 ─────────────────────────────────────────────────
#
# 서비스 밖에 둔다. 워커가 부르는데, 워커에는 액터가 없다.


async def totals_of(session: AsyncSession, sprint_id: UUID) -> SprintTotals:
    """지금 이 스프린트에 들어 있는 양.

    추정이 없는 이슈는 0 분으로 센다. `NULL` 을 빼면 "추정 안 한 이슈가 많은
    스프린트" 가 실제보다 가벼워 보이는데, 그건 개수 축이 말해 준다.
    """
    row = (
        await session.execute(
            select(
                func.count(Issue.id),
                func.coalesce(func.sum(func.coalesce(Issue.estimate_minutes, 0)), 0),
                func.count(Issue.id).filter(WorkflowState.category != "done"),
                func.coalesce(
                    func.sum(func.coalesce(Issue.estimate_minutes, 0)).filter(
                        WorkflowState.category != "done"
                    ),
                    0,
                ),
            )
            .select_from(Issue)
            .join(WorkflowState, WorkflowState.id == Issue.state_id)
            .where(Issue.sprint_id == sprint_id, Issue.archived_at.is_(None))
        )
    ).one()
    return SprintTotals(
        issues=int(row[0] or 0),
        minutes=int(row[1] or 0),
        remaining_issues=int(row[2] or 0),
        remaining_minutes=int(row[3] or 0),
    )


async def snapshot_one(session: AsyncSession, sprint: Sprint) -> None:
    """오늘 줄을 쓴다. 있으면 덮어쓴다 — **오늘 점은 살아 있다.**

    날짜가 바뀌면 그 줄은 그대로 굳는다. 그래서 지난 날들은 그날 찍힌 값이고,
    되짚어 계산한 값이 아니다.
    """
    found = await totals_of(session, sprint.id)
    stmt = insert(SprintSnapshot).values(
        sprint_id=sprint.id,
        on_date=utcnow().date(),
        remaining_issues=found.remaining_issues,
        remaining_minutes=found.remaining_minutes,
        total_issues=found.issues,
        total_minutes=found.minutes,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[SprintSnapshot.sprint_id, SprintSnapshot.on_date],
            set_={
                "remaining_issues": stmt.excluded.remaining_issues,
                "remaining_minutes": stmt.excluded.remaining_minutes,
                "total_issues": stmt.excluded.total_issues,
                "total_minutes": stmt.excluded.total_minutes,
            },
        )
    )


async def snapshot_sprints(session: AsyncSession) -> int:
    """도는 스프린트 전부에 오늘 점을 찍는다. 워커가 부른다.

    **닫힌 것은 안 건드린다.** 닫을 때 마지막 점을 이미 찍었고, 그 뒤에 또
    찍으면 옮겨 간 이슈 때문에 값이 움직인다 — 지난 기록이 바뀌면 안 된다.
    """
    rows = list(
        (await session.execute(select(Sprint).where(Sprint.state == "active"))).scalars().all()
    )
    for row in rows:
        await snapshot_one(session, row)
    return len(rows)


def _clean_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise ValidationError("이름을 비울 수 없다.", code="issues.sprint_name_empty")
    if len(clean) > MAX_NAME:
        raise ValidationError(f"이름은 {MAX_NAME}자까지다.", code="issues.sprint_name_too_long")
    return clean


def _check_window(starts_at: datetime | None, ends_at: datetime | None) -> None:
    """끝이 시작보다 앞서면 거절한다. **번다운의 축이 거꾸로 간다.**"""
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValidationError("끝나는 때가 시작보다 앞선다.", code="issues.sprint_window_invalid")
