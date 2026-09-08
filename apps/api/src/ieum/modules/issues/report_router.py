"""리포트 라우터 (A29, M5)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.reports import GROUPS, Bucket, CountReport, ReportService

reports_router = APIRouter(prefix="/reports", tags=["reports"])


class CountRequest(BaseModel):
    """세어 달라는 요청.

    POST 인 이유: IQL 이 길면 URL 상한에 걸린다 — 검색(`/search/issues`)이
    같은 이유로 POST 다. 읽기지만 몸통이 필요하다.
    """

    model_config = ConfigDict(extra="forbid")

    #: 빈 문자열이면 볼 수 있는 것 전부. ACL 은 언제나 얹힌다.
    iql: str = Field(default="", max_length=4000)
    group_by: str = Field(min_length=1, max_length=64)


class BucketResponse(BaseModel):
    #: `null` 이면 **"값이 없는 것들"** 이다 — 빈 칸이 아니라 하나의 칸이다.
    #: 화면은 "담당자 없음" 처럼 이름을 붙여야 한다.
    key: str | None
    count: int

    @classmethod
    def of(cls, bucket: Bucket) -> BucketResponse:
        return cls(key=bucket.key, count=bucket.count)


class CountResponse(BaseModel):
    group_by: str
    buckets: list[BucketResponse]
    #: 조건에 맞는 이슈 수.
    #:
    #: **`multi_valued` 가 거짓이면 칸의 합과 같다.** 안 맞으면 리포트가
    #: 거짓말을 하는 것이고, 그때는 이 값을 믿어야 한다.
    total: int
    #: 이슈 하나가 여러 칸에 들어갈 수 있나(라벨). 그러면 합 > 총계다.
    multi_valued: bool
    #: 칸 상한에서 잘렸나. 잘렸으면 합이 총계보다 작다 — 그 이유가 이것이다.
    truncated: bool
    #: 셀 수 있는 기준 전부. 화면이 목록을 손으로 들지 않게 한다.
    supported: list[str]

    @classmethod
    def of(cls, report: CountReport) -> CountResponse:
        return cls(
            group_by=report.group_by,
            buckets=[BucketResponse.of(b) for b in report.buckets],
            total=report.total,
            multi_valued=report.multi_valued,
            truncated=report.truncated,
            supported=sorted(GROUPS),
        )


@reports_router.post("/count", response_model=CountResponse)
async def count_issues(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: CountRequest,
) -> CountResponse:
    """IQL 이 고른 것을 기준별로 센다.

    검색과 **같은 컴파일러·같은 ACL** 을 탄다. 세는 길이 따로 조건을 만들면
    언젠가 어긋나고, 그때 리포트와 목록이 다른 숫자를 말한다.
    """
    report = await ReportService(session, permissions).count(
        actor, iql=payload.iql, group_by=payload.group_by
    )
    return CountResponse.of(report)


__all__ = ["reports_router"]
