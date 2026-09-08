"""반복 이슈 — 스케줄로 이슈를 만든다 (A27, M5).

스케줄 계산은 `recurrence.py` 의 순수 함수가 한다. 여기 있는 것은 **틀을
관리하는 일**과 **때가 됐을 때 실제로 만드는 일**이다.

## 두 번 만들지 않는다

만드는 것과 다음 시각 전진이 **같은 트랜잭션**이다. 나누면 워커가 그 사이에
죽었을 때 다음 주기가 같은 이슈를 또 만든다. 그리고 훑을 때 행 잠금을 건다
(`FOR UPDATE SKIP LOCKED`) — 워커가 둘이면 같은 스케줄을 둘이 집는다.

`SKIP LOCKED` 인 이유: 다른 워커가 이미 잡은 스케줄은 **기다릴 것이 아니라
넘길 것**이다. 기다리면 15초 주기가 남의 이슈 생성에 묶인다.

## 밀린 만큼 몰아 만들지 않는다

다음 시각을 `지금` 기준으로 다시 잡는다. 놓친 시각 기준으로 잡으면 앱이
일주일 내려갔다 올라온 날 매일 스케줄이 7건을 만든다 — 그건 복구가 아니라
알림 폭탄이고, 받는 사람은 7건을 다 지운 뒤 스케줄을 끈다.

## 만든 사람이 신고자다

스케줄은 프로젝트의 것이지만 이슈에는 사람이 적혀야 한다. 만든 사람의 계정이
정지되면 **스케줄을 끄고 이유를 적는다** — 없는 사람 이름으로 이슈가 계속
만들어지는 것도, 조용히 멈추는 것도 옳지 않다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, IeumError, NotFoundError, ValidationError
from ieum.core.logging import get_logger
from ieum.core.permissions import PermissionService, Scope, get_permission_service
from ieum.core.time import utcnow
from ieum.db.session import session_scope
from ieum.modules.identity import contracts as identity
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.models import RecurringIssue
from ieum.modules.issues.recurrence import Schedule, next_after, validate

log = get_logger(__name__)

MAX_NAME = 200
MAX_SUMMARY = 500
MAX_LABELS = 20
#: 한 주기에 만들 이슈 수의 상한. 스케줄이 많아도 15초 주기를 넘기지 않는다.
BATCH = 50

#: 만든 사람의 계정이 더 이상 활성이 아니다.
#:
#: **`last_error` 는 언제나 에러 코드다.** 스케줄이 스스로 멈추는 이유는 대개
#: 이슈를 만들려다 거절당한 것이고(접힌 프로젝트, 잃은 배정 권한), 그건 이미
#: 코드를 갖고 있다. 이 경우만 짧은 사유로 두면 화면이 두 규칙을 알아야 하므로,
#: 이것도 코드로 만들어 **한 규칙**으로 읽는다 — 화면은 `errors` 카탈로그로
#: 번역한다.
STOPPED_OWNER_INACTIVE = "issues.recurrence_owner_inactive"


@dataclass(frozen=True, slots=True)
class NewRecurrence:
    project_id: UUID
    name: str
    summary: str
    schedule: Schedule
    description: str | None = None
    type_id: UUID | None = None
    assignee_id: UUID | None = None
    priority: int = 3
    labels: tuple[str, ...] = ()
    due_in_days: int | None = None


class RecurringIssueService:
    """반복 스케줄을 만들고 고치고 끈다."""

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def list_for(self, actor: Actor, project_id: UUID) -> list[RecurringIssue]:
        """이 프로젝트의 스케줄 전부. **꺼진 것도 준다** — 왜 안 도는지 보려면
        목록에 있어야 한다."""
        await self._require(actor, project_id, perms.ISSUE_VIEW)
        return list(
            (
                await self._s.execute(
                    select(RecurringIssue)
                    .where(RecurringIssue.project_id == project_id)
                    .order_by(RecurringIssue.next_run_at, RecurringIssue.name)
                )
            )
            .scalars()
            .all()
        )

    async def create(self, actor: Actor, payload: NewRecurrence) -> RecurringIssue:
        await self._require(actor, payload.project_id, perms.ISSUE_CREATE)
        validate(payload.schedule)
        row = RecurringIssue(
            project_id=payload.project_id,
            name=_name(payload.name),
            summary=_summary(payload.summary),
            description=payload.description,
            type_id=payload.type_id,
            assignee_id=payload.assignee_id,
            priority=payload.priority,
            labels=_labels(payload.labels),
            due_in_days=_due_in_days(payload.due_in_days),
            cadence=payload.schedule.cadence,
            hour=payload.schedule.hour,
            minute=payload.schedule.minute,
            weekday=payload.schedule.weekday,
            day=payload.schedule.day,
            timezone=payload.schedule.timezone,
            # **처음 실행도 지금 이후다.** 만든 즉시 한 번 만들면 사람은
            # 시험용으로 켰다가 실제 이슈를 받는다.
            next_run_at=next_after(payload.schedule, utcnow()),
            created_by=actor.user_id,
        )
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "같은 이름의 반복이 이미 있다.", code="issues.recurrence_name_taken"
            ) from exc
        log.info(
            "issues.recurrence_created",
            actor=str(actor.user_id),
            project=str(payload.project_id),
            recurrence=str(row.id),
        )
        return row

    async def update(
        self,
        actor: Actor,
        recurrence_id: UUID,
        *,
        payload: NewRecurrence | None = None,
        is_enabled: bool | None = None,
    ) -> RecurringIssue:
        row = await self._require_row(actor, recurrence_id, perms.ISSUE_CREATE)
        if payload is not None:
            validate(payload.schedule)
            row.name = _name(payload.name)
            row.summary = _summary(payload.summary)
            row.description = payload.description
            row.type_id = payload.type_id
            row.assignee_id = payload.assignee_id
            row.priority = payload.priority
            row.labels = _labels(payload.labels)
            row.due_in_days = _due_in_days(payload.due_in_days)
            row.cadence = payload.schedule.cadence
            row.hour = payload.schedule.hour
            row.minute = payload.schedule.minute
            row.weekday = payload.schedule.weekday
            row.day = payload.schedule.day
            row.timezone = payload.schedule.timezone
            # 주기를 고쳤으면 다음 시각도 다시 잡는다. 안 잡으면 사람은 새
            # 주기를 적었는데 다음 한 번은 옛 시각에 돈다.
            row.next_run_at = next_after(payload.schedule, utcnow())
        if is_enabled is not None:
            row.is_enabled = is_enabled
            if is_enabled:
                # 사람이 다시 켰으면 스스로 껐던 이유는 지운다. 남겨 두면
                # 화면이 도는 스케줄에 계속 "정지됨" 이유를 붙여 보여 준다.
                row.last_error = None
                row.next_run_at = next_after(schedule_of(row), utcnow())
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "같은 이름의 반복이 이미 있다.", code="issues.recurrence_name_taken"
            ) from exc
        return row

    async def delete(self, actor: Actor, recurrence_id: UUID) -> None:
        row = await self._require_row(actor, recurrence_id, perms.ISSUE_CREATE)
        await self._s.delete(row)
        await self._s.flush()

    async def _require_row(
        self, actor: Actor, recurrence_id: UUID, permission: str
    ) -> RecurringIssue:
        row = await self._s.get(RecurringIssue, recurrence_id)
        if row is None:
            raise NotFoundError("반복을 찾을 수 없다.")
        await self._require(actor, row.project_id, permission)
        return row

    async def _require(self, actor: Actor, project_id: UUID, permission: str) -> None:
        await self._perms.require(self._s, actor, permission, scope=Scope.project(project_id))


def schedule_of(row: RecurringIssue) -> Schedule:
    """행에서 스케줄 값을 되만든다. 계산은 언제나 순수 함수가 한다."""
    return Schedule(
        cadence=row.cadence,  # type: ignore[arg-type]
        hour=row.hour,
        minute=row.minute,
        timezone=row.timezone,
        weekday=row.weekday,
        day=row.day,
    )


def _name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise ValidationError("반복 이름이 필요하다.", code="issues.recurrence_name_required")
    return name[:MAX_NAME]


def _summary(raw: str) -> str:
    summary = raw.strip()
    if not summary:
        raise ValidationError(
            "만들 이슈의 요약이 필요하다.", code="issues.recurrence_summary_required"
        )
    return summary[:MAX_SUMMARY]


def _labels(raw: tuple[str, ...] | list[str]) -> list[str]:
    seen = [label.strip() for label in raw if label.strip()]
    return list(dict.fromkeys(seen))[:MAX_LABELS]


def _due_in_days(raw: int | None) -> int | None:
    """기한을 며칠 뒤로. 음수는 거절한다 — 만드는 순간 이미 지난 기한이다."""
    if raw is None:
        return None
    if raw < 0 or raw > 3650:
        raise ValidationError(
            "기한은 0일 이상 3650일 이하로 둔다.",
            code="issues.recurrence_bad_due",
            details={"value": raw},
        )
    return raw


# ── 때가 된 것을 만든다 ─────────────────────────────────────────
#
# 서비스 밖에 둔다. 워커가 부르는데, 워커에는 액터가 없다.


async def run_due_recurrences() -> int:
    """지금 지난 스케줄을 돌린다. 만든 건수를 돌려준다.

    **권한 서비스를 전역에서 꺼내는 것은 여기까지다.** 아래 `run_due` 는
    받아서 쓴다 — 전역을 안쪽에서 부르면 시험이 앱을 통째로 세워야 한다.
    """
    permissions = get_permission_service()
    async with session_scope() as session:
        return await run_due(session, permissions)


async def run_due(session: AsyncSession, permissions: PermissionService) -> int:
    """때가 된 스케줄을 돌린다. 세션과 권한을 받는다(워커가 넘긴다).

    **권한을 실제로 본다.** `create_authorized` 는 `issue.create` 를 건너뛰지만
    담당자를 지정할 때는 `issue.assign` 을 본다 — 스케줄을 만든 사람이 그
    권한을 잃으면 조용히 배정되는 것이 아니라 거절돼야 한다. 그래서 워커도
    앱과 같은 배선을 쓴다 (`wiring.install_permissions`).
    """
    # 늦은 import: 서비스가 이 모듈을 쓰지 않으므로 순환을 만들지 않는다.
    from ieum.modules.issues.service import IssueService, NewIssue

    now = utcnow()
    rows = list(
        (
            await session.execute(
                select(RecurringIssue)
                .where(RecurringIssue.is_enabled, RecurringIssue.next_run_at <= now)
                .order_by(RecurringIssue.next_run_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for row in rows:
        try:
            # **저장된 스케줄이 아직 계산 가능한가.** `next_after` 는 시간대를
            # 그대로 `ZoneInfo` 에 넘기는데, 그건 `IeumError` 가 아닌 예외를
            # 낸다 — tzdata 가 그 이름을 버린 날, 또는 누가 DB 를 직접 고친
            # 날, 아래 `except` 를 그냥 지나쳐 **배치 전체가 되돌아간다.**
            # 여기서 미리 보면 그 줄만 꺼지고 이유도 코드로 남는다.
            validate(schedule_of(row))
            owner = await _owner(session, row)
            view = await IssueService(session, permissions).create_authorized(
                owner,
                NewIssue(
                    project_id=row.project_id,
                    summary=row.summary,
                    type_id=row.type_id,
                    description=row.description,
                    assignee_id=row.assignee_id,
                    priority=row.priority,
                    due_date=(
                        None
                        if row.due_in_days is None
                        else (now + timedelta(days=row.due_in_days)).date()
                    ),
                    labels=list(row.labels),
                ),
            )
        except IeumError as exc:
            # **한 스케줄의 고장이 나머지를 막지 않는다.**
            #
            # 프로젝트가 접혔거나(`ConflictError`) 만든 사람이 배정 권한을
            # 잃었으면(`PermissionDeniedError`) 이 스케줄은 더 못 돈다. 그
            # 예외를 그냥 올리면 트랜잭션이 통째로 되돌아가고, 접힌 프로젝트
            # 하나가 **설치 전체의 반복을 영원히 멈춘다** — 그리고 그 사실은
            # 워커 로그에만 남는다.
            #
            # 그래서 그 줄만 끄고 이유를 남기고 계속 간다. 사람이 목록에서
            # 읽고, 고친 뒤 다시 켠다.
            row.is_enabled = False
            row.last_error = exc.code
            log.warning(
                "issues.recurrence_disabled",
                recurrence=str(row.id),
                reason=exc.code,
            )
            continue

        row.last_run_at = now
        row.last_issue_id = view.issue.id
        # **`지금` 기준으로 다시 잡는다.** 놓친 시각 기준으로 잡으면 밀린 만큼
        # 몰아 만든다 (모듈 주석 참조).
        row.next_run_at = next_after(schedule_of(row), now)
        created += 1
        log.info(
            "issues.recurrence_fired",
            recurrence=str(row.id),
            issue=str(view.issue.id),
            next_run_at=row.next_run_at.isoformat(),
        )
    return created


async def _owner(session: AsyncSession, row: RecurringIssue) -> Actor:
    """이슈에 신고자로 적을 사람.

    계정이 없거나 정지됐으면 거절한다 — 없는 사람 이름으로 이슈가 계속
    만들어지는 것도, 조용히 멈추는 것도 옳지 않다. 부르는 쪽이 이 거절을
    받아 스케줄을 끄고 이유를 남긴다.
    """
    found = {} if row.created_by is None else await identity.get_users(session, {row.created_by})
    user = None if row.created_by is None else found.get(row.created_by)
    if user is None or not user.is_active:
        raise ValidationError(
            "반복을 만든 계정이 더 이상 활성이 아니다.",
            code=STOPPED_OWNER_INACTIVE,
        )
    return Actor(user_id=user.id, email=user.email, is_active=True)


__all__ = [
    "BATCH",
    "STOPPED_OWNER_INACTIVE",
    "NewRecurrence",
    "RecurringIssueService",
    "run_due",
    "run_due_recurrences",
    "schedule_of",
]
