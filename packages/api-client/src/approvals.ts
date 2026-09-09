/**
 * 승인 단계 — 요청 유형이 요구하고, 명단에 찍힌 사람이 결정한다 (C12, M6).
 */

import type { ApiClient } from './client'

export type ApprovalMode = 'one' | 'all'
export type ApprovalStatus = 'pending' | 'approved' | 'declined' | 'cancelled'
export type ApprovalDecision = 'approve' | 'decline'

/** 요청 유형에 붙는 규칙. `request_type.approval` 이다. */
export interface ApprovalRule {
  mode: ApprovalMode
  user_ids: string[]
  group_ids: string[]
}

export interface Approver {
  user_id: string
  display_name: string
}

export interface ApprovalVote {
  user_id: string
  display_name: string
  decision: ApprovalDecision
  comment: string | null
  decided_at: string
}

export interface Approval {
  id: string
  issue_id: string
  issue_key: string
  summary: string
  mode: ApprovalMode
  status: ApprovalStatus
  requested_at: string
  decided_at: string | null
  /**
   * **찍어 둔 명단이다.** 그룹을 그때그때 펼친 것이 아니라 요청하는 순간의
   * 사람들이다 — 그래서 비어 있을 수 있고(그룹이 비었거나 요청자뿐이었다),
   * 그때 화면은 "승인자가 없다" 를 보여 줘야 한다.
   */
  approvers: Approver[]
  votes: ApprovalVote[]
  /** 지금 보는 사람이 결정할 수 있나. 버튼을 낼 근거다. */
  can_decide: boolean
}

const BASE = '/api/v1/approvals'

export function createApprovalsApi(client: ApiClient) {
  return {
    /** 이 티켓의 승인 이력. 최신순. */
    forIssue: (issueId: string) =>
      client.get<Approval[]>(`${BASE}?issue_id=${encodeURIComponent(issueId)}`),
    /** 내가 결정해야 할 것. 아직 안 낸 것만 온다. */
    mine: () => client.get<Approval[]>(`${BASE}/mine`),
    decide: (id: string, body: { decision: ApprovalDecision; comment?: string | null }) =>
      client.post<Approval>(`${BASE}/${id}/decision`, body),
    /** 기다리는 승인을 접는다. 멈춘 티켓을 푸는 유일한 길이다. */
    cancel: (id: string) => client.post<Approval>(`${BASE}/${id}/cancel`, {}),
  }
}

export type ApprovalsApi = ReturnType<typeof createApprovalsApi>
