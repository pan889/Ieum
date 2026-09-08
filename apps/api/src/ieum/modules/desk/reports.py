"""데스크 리포트 — SLA 와 상담원 성과 (C14, M5).

`sla_clock` 이 이미 답을 갖고 있다. **세는 규칙을 틀리지 않는 것이 전부다.**

## 위반은 `target_at` 과 지금을 비교해서 정한다

`breached_at` 은 "알렸다" 는 뜻이다(모델 주석에 그렇게 적혀 있다). 스윕이
15초마다 도니까 아직 안 훑은 위반은 그 값이 비어 있고, 그걸로 세면 위반이
**적게** 나온다 — 그리고 적게 나오는 방향이 하필 듣기 좋은 방향이다.

## 안 끝났는데 목표를 지난 시계도 위반이다

`completed_at IS NOT NULL` 만 세면 **지금 터지고 있는 것**이 안 보인다. 그건
리포트가 가장 크게 도움이 될 자리다. 그래서 위반을 둘로 나눠 준다: 끝났는데
늦은 것과, 아직 안 끝났는데 이미 지난 것.

## 평균 해결 시간은 벽시계다 — SLA 와 같은 시간이 아니다

SLA 는 업무 달력으로 재고(`docs/architecture/data-model.md`), 이 평균은 실제
경과 시간이다. 둘을 같은 이름으로 부르면 "평균 2시간" 을 "SLA 4시간" 과
비교하게 되는데 그건 다른 축이다 — 그래서 이름에 `wallclock` 을 박았다.

## 몇 건 중 몇 건을 셌는지 말한다

평균은 **끝난 것만** 정의된다. 모집단을 안 밝히면 "평균 2시간" 이 전체를
뜻하는 것처럼 읽힌다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ValidationError
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as perms
from ieum.modules.desk.models import SlaClock, SlaPolicy, TicketExt
from ieum.modules.issues import contracts as issues

#: 한 번에 볼 수 있는 기간. SLA 시계는 티켓마다 정책마다 하나씩 있어서
#: 창이 넓으면 훑는 양이 빠르게 는다.
MAX_DAYS = 366


@dataclass(frozen=True, slots=True)
class SlaOutcome:
    """정책 하나의 성적."""

    policy_id: UUID
    policy_name: str
    metric: str
    #: 약속을 지킨 것. 끝났고 목표 안이다.
    met: int
    #: 끝났지만 늦은 것.
    missed: int
    #: **아직 안 끝났는데 이미 목표를 지난 것.** 지금 터지고 있다.
    overdue: int
    #: 아직 안 끝났고 목표도 안 지난 것.
    running: int

    @property
    def total(self) -> int:
        return self.met + self.missed + self.overdue + self.running

    @property
    def breached(self) -> int:
        """위반 전부 — 늦게 끝난 것과 지금 지나 있는 것."""
        return self.missed + self.overdue


@dataclass(frozen=True, slots=True)
class AgentRow:
    """상담원 한 명의 성적.

    `assignee_id` 가 `None` 이면 **담당자 없는 티켓들**이다 — 빈 칸이 아니라
    하나의 칸이고, 보통 가장 봐야 할 무리다.
    """

    assignee_id: UUID | None
    #: 이 창에 만들어진 티켓 중 이 사람에게 걸린 것.
    tickets: int
    #: 그중 끝난 것.
    resolved: int
    #: 끝난 것들의 **벽시계** 평균(초). 하나도 없으면 `None`.
    #:
    #: SLA 의 업무 시간과 **다른 축이다.** 같은 이름으로 부르면 안 된다.
    average_wallclock_seconds: int | None


@dataclass(slots=True)
class DeskReport:
    starts_at: datetime
    ends_at: datetime
    #: 창 안에 만들어진 티켓 수. 아래 셈들의 검산 근거다.
    tickets: int
    sla: list[SlaOutcome] = field(default_factory=list)
    agents: list[AgentRow] = field(default_factory=list)


class DeskReportService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def report(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime | None = None,
    ) -> DeskReport:
        """이 창에 **만들어진** 티켓을 기준으로 센다.

        "이 창에 끝난 것" 이 아니다: 끝난 것만 세면 아직 안 끝난 티켓이
        리포트에서 사라지고, 그게 바로 위반이 숨는 방식이다.
        """
        await self._perms.require(self._s, actor, perms.QUEUE_WORK, scope=Scope.project(project_id))
        _check_window(starts_at, ends_at)
        moment = now or utcnow()

        # **이슈 컬럼은 `issues` 가 본다.** 데스크는 "티켓인 이슈만" 을
        # 넘기고(큐가 `run_iql` 에 넘기는 것과 같은 방식), 세는 것과 창을
        # 좁히는 것은 계약이 한다 — 모듈 경계가 그렇게 정해져 있다(ADR-0010).
        facts = await issues.window_agent_facts(
            self._s,
            project_id=project_id,
            starts_at=starts_at,
            ends_at=ends_at,
            extra_where=self._tickets_only(),
        )
        in_window = issues.window_issue_ids(
            project_id=project_id,
            starts_at=starts_at,
            ends_at=ends_at,
            extra_where=self._tickets_only(),
        )

        return DeskReport(
            starts_at=starts_at,
            ends_at=ends_at,
            # 담당자별 합이 곧 창 안 티켓 수다. 따로 세면 두 값이 어긋날 수
            # 있고, 그때 어느 쪽이 진실인지 말할 수 없다.
            tickets=sum(fact.total for fact in facts),
            sla=await self._sla(in_window, moment),
            agents=[
                AgentRow(
                    assignee_id=fact.assignee_id,
                    tickets=fact.total,
                    resolved=fact.resolved,
                    average_wallclock_seconds=fact.average_wallclock_seconds,
                )
                for fact in facts
            ],
        )

    @staticmethod
    def _tickets_only() -> Any:
        """ "티켓인 이슈만" 조건. 큐(`service.QueueService`)와 같은 모양이다."""
        return (
            select(TicketExt.issue_id)
            .where(TicketExt.issue_id == issues.issue_id_column())
            .exists()
        )

    async def _sla(self, in_window: Any, moment: datetime) -> list[SlaOutcome]:
        """정책별 네 갈래.

        **`breached_at` 을 안 본다.** 그 값은 알림 중복을 막는 표시이고,
        스윕이 아직 안 훑은 위반은 비어 있다.
        """
        done = SlaClock.completed_at.is_not(None)
        late = SlaClock.completed_at > SlaClock.target_at
        past = SlaClock.target_at < moment

        stmt: Select[Any] = (
            select(
                SlaPolicy.id,
                SlaPolicy.name,
                SlaPolicy.metric,
                func.count(case((and_(done, ~late), 1))).label("met"),
                func.count(case((and_(done, late), 1))).label("missed"),
                func.count(case((and_(~done, past), 1))).label("overdue"),
                func.count(case((and_(~done, ~past), 1))).label("running"),
            )
            .select_from(SlaClock)
            .join(SlaPolicy, SlaPolicy.id == SlaClock.policy_id)
            .where(SlaClock.issue_id.in_(in_window))
            .group_by(SlaPolicy.id, SlaPolicy.name, SlaPolicy.metric)
            .order_by(SlaPolicy.name)
        )
        return [
            SlaOutcome(
                policy_id=row.id,
                policy_name=row.name,
                metric=row.metric,
                met=int(row.met),
                missed=int(row.missed),
                overdue=int(row.overdue),
                running=int(row.running),
            )
            for row in (await self._s.execute(stmt)).all()
        ]


def _check_window(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at < starts_at:
        raise ValidationError("끝나는 때가 시작보다 앞선다.", code="desk.report_window_invalid")
    if (ends_at - starts_at).days + 1 > MAX_DAYS:
        raise ValidationError(
            f"한 번에 볼 수 있는 기간은 {MAX_DAYS}일까지다.",
            code="desk.report_window_too_wide",
            details={"max_days": MAX_DAYS},
        )


__all__ = ["MAX_DAYS", "AgentRow", "DeskReport", "DeskReportService", "SlaOutcome"]
