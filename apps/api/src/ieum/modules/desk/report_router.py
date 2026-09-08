"""데스크 리포트 라우터 (C14, M5)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.desk.reports import AgentRow, DeskReport, DeskReportService, SlaOutcome

desk_reports_router = APIRouter(prefix="/desk-reports", tags=["desk"])


class SlaOutcomeResponse(BaseModel):
    policy_id: UUID
    policy_name: str
    metric: str
    #: 끝났고 목표 안.
    met: int
    #: 끝났지만 늦음.
    missed: int
    #: **아직 안 끝났는데 이미 목표를 지났다.** 지금 터지고 있다.
    #:
    #: 이걸 따로 주는 이유: `missed` 만 보면 "이미 벌어진 일" 이고, 이 값은
    #: **지금 손쓸 수 있는 일**이다. 리포트가 가장 도움 되는 자리다.
    overdue: int
    #: 아직 안 끝났고 목표도 안 지났다.
    running: int
    #: 위반 전부 = `missed + overdue`. 화면이 더하지 않게 서버가 준다.
    breached: int
    #: 네 갈래의 합. 화면이 검산할 수 있게 준다.
    total: int

    @classmethod
    def of(cls, row: SlaOutcome) -> SlaOutcomeResponse:
        return cls(
            policy_id=row.policy_id,
            policy_name=row.policy_name,
            metric=row.metric,
            met=row.met,
            missed=row.missed,
            overdue=row.overdue,
            running=row.running,
            breached=row.breached,
            total=row.total,
        )


class AgentRowResponse(BaseModel):
    #: `null` 이면 **담당자 없는 티켓들**이다 — 빈 칸이 아니라 하나의 칸이고,
    #: 보통 가장 봐야 할 무리다.
    assignee_id: UUID | None
    tickets: int
    resolved: int
    #: 끝난 것들의 **벽시계** 평균(초). 하나도 없으면 `null`.
    #:
    #: SLA 는 업무 달력으로 재고 이건 실제 경과 시간이다 — **다른 축이다.**
    #: 이름에 `wallclock` 이 박힌 이유이고, 화면도 그렇게 적어야 한다.
    #: `0` 이 아니라 `null` 인 이유: 0 은 "즉시 해결" 로 읽힌다.
    average_wallclock_seconds: int | None

    @classmethod
    def of(cls, row: AgentRow) -> AgentRowResponse:
        return cls(
            assignee_id=row.assignee_id,
            tickets=row.tickets,
            resolved=row.resolved,
            average_wallclock_seconds=row.average_wallclock_seconds,
        )


class DeskReportResponse(BaseModel):
    starts_at: datetime
    ends_at: datetime
    #: 창 안에 **만들어진** 티켓 수. 아래 셈들의 검산 근거다.
    #:
    #: "끝난 것" 기준이 아닌 이유: 끝난 것만 세면 아직 안 끝난 티켓이
    #: 리포트에서 사라지고, 그게 위반이 숨는 방식이다.
    tickets: int
    sla: list[SlaOutcomeResponse]
    agents: list[AgentRowResponse]

    @classmethod
    def of(cls, report: DeskReport) -> DeskReportResponse:
        return cls(
            starts_at=report.starts_at,
            ends_at=report.ends_at,
            tickets=report.tickets,
            sla=[SlaOutcomeResponse.of(r) for r in report.sla],
            agents=[AgentRowResponse.of(r) for r in report.agents],
        )


@desk_reports_router.get("", response_model=DeskReportResponse)
async def read_desk_report(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    project_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> DeskReportResponse:
    """SLA 성적과 상담원별 성적.

    위반은 `target_at` 과 지금을 비교해서 정한다 — `breached_at` 은 "알렸다"
    는 뜻이고, 스윕이 아직 안 훑은 위반은 그 값이 비어 있다.
    """
    report = await DeskReportService(session, permissions).report(
        actor, project_id=project_id, starts_at=starts_at, ends_at=ends_at
    )
    return DeskReportResponse.of(report)


__all__ = ["desk_reports_router"]
