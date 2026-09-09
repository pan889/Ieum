"""승인 라우터 (C12).

`router.py` 와 나눈 이유: 승인의 근거는 **권한이 아니라 명단**이다. 승인자로
찍힌 사람은 그 프로젝트의 데스크 권한이 없을 수 있고(다른 부서의 장), 그래도
결정은 해야 한다. 그 판단이 권한 검사로 가득한 파일 안에 섞여 있으면 다음에
읽는 사람이 `require` 하나를 "빠뜨린 것" 으로 보고 채워 넣는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.desk.approvals import ApprovalService, ApprovalView
from ieum.modules.desk.models import APPROVAL_DECISIONS

approvals_router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApproverResponse(BaseModel):
    # **중첩된 것에도 붙여야 한다.** 바깥에만 붙이면 목록 항목이 dataclass
    # 그대로 들어와 pydantic 이 거절한다 — 실제로 그렇게 500 이 났다.
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    display_name: str


class VoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    display_name: str
    decision: str
    comment: str | None
    decided_at: datetime


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    issue_id: UUID
    issue_key: str
    summary: str
    mode: str
    status: str
    requested_at: datetime
    decided_at: datetime | None
    approvers: list[ApproverResponse]
    votes: list[VoteResponse]
    #: 지금 보는 사람이 결정할 수 있나. 화면이 버튼을 낼 근거다.
    can_decide: bool

    @classmethod
    def of(cls, view: ApprovalView) -> ApprovalResponse:
        return cls.model_validate(view)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=f"^({'|'.join(APPROVAL_DECISIONS)})$")
    #: 거절 이유. 강제하지 않는다 — 승인에는 필요 없다.
    comment: str | None = Field(default=None, max_length=2000)


@approvals_router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    issue_id: UUID,
) -> list[ApprovalResponse]:
    """이 티켓의 승인 이력. 최신순."""
    found = await ApprovalService(session, permissions).for_issue(actor, issue_id)
    return [ApprovalResponse.of(view) for view in found]


@approvals_router.get("/mine", response_model=list[ApprovalResponse])
async def my_approvals(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ApprovalResponse]:
    """내가 결정해야 할 것.

    **`/{approval_id}` 형제보다 위에 있어야 한다** — 아래로 내려가면 `mine`
    이 승인 id 로 잡혀 UUID 파싱 오류가 난다 (conventions "고정 경로는
    형제 전부보다 위에 둔다").
    """
    found = await ApprovalService(session, permissions).mine(actor, limit=limit)
    return [ApprovalResponse.of(view) for view in found]


@approvals_router.post("/{approval_id}/decision", response_model=ApprovalResponse)
async def decide_approval(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    approval_id: UUID,
    payload: DecisionRequest,
) -> ApprovalResponse:
    """승인하거나 거절한다. **찍어 둔 명단에 있는 사람만.**"""
    view = await ApprovalService(session, permissions).decide(
        actor, approval_id, decision=payload.decision, comment=payload.comment
    )
    await session.commit()
    return ApprovalResponse.of(view)


@approvals_router.post("/{approval_id}/cancel", response_model=ApprovalResponse)
async def cancel_approval(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    approval_id: UUID,
) -> ApprovalResponse:
    """기다리는 승인을 접는다. 멈춘 티켓을 푸는 유일한 길이다."""
    view = await ApprovalService(session, permissions).cancel(actor, approval_id)
    await session.commit()
    return ApprovalResponse.of(view)


__all__ = ["approvals_router"]
