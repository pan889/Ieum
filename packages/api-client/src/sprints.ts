/**
 * 스프린트와 번다운 (M5).
 *
 * 스프린트는 **기간이 있는 묶음**이다. 남은 일을 시간축에 놓는 것이
 * 번다운이므로, 시간이 먼저 있어야 한다.
 */

import type { ApiClient } from './client'

export type SprintState = 'future' | 'active' | 'closed'

export interface Sprint {
  id: string
  project_id: string
  name: string
  goal: string | null
  state: SprintState
  /** 계획한 기간. */
  starts_at: string | null
  ends_at: string | null
  /** **실제로** 시작·끝난 때. 번다운은 이쪽을 쓴다. */
  activated_at: string | null
  closed_at: string | null
  /** 지금 들어 있는 양. 목록에서 바로 읽혀야 계획 회의에 쓸 수 있다. */
  issues: number
  minutes: number
  remaining_issues: number
  remaining_minutes: number
}

/**
 * 도는 스프린트 + **어느 프로젝트의 것인가.**
 *
 * 첫 화면은 여러 프로젝트를 섞어 보여 준다. "Sprint 3" 만 있으면 그게 누구의
 * Sprint 3 인지 알 수 없으므로 서버가 프로젝트를 함께 준다 — 화면이 행마다
 * 물어보면 목록 길이만큼 왕복이 늘어난다.
 */
export interface ActiveSprint extends Sprint {
  project_key: string
  project_name: string
}

/** 그날 찍힌 점. **되짚어 계산한 값이 아니다.** */
export interface BurndownPoint {
  on_date: string
  remaining_issues: number
  remaining_minutes: number
  /** 그날 스프린트에 들어 있던 전체. 범위가 늘어난 것이 여기서 보인다. */
  total_issues: number
  total_minutes: number
}

export interface NewSprint {
  project_id: string
  name: string
  goal?: string | null
  starts_at?: string | null
  ends_at?: string | null
}

/**
 * 고칠 것만 보낸다.
 *
 * **`null` 로는 못 지운다.** JSON 에 "값 없음" 과 `null` 을 가릴 방법이 없어서,
 * 지우는 것은 `clear_*` 로 말한다 — 보드(`clear_swimlane`)와 같은 방식이다.
 */
export interface SprintPatch {
  name?: string
  goal?: string
  starts_at?: string
  ends_at?: string
  clear_goal?: boolean
  clear_starts_at?: boolean
  clear_ends_at?: boolean
}

const BASE = '/api/v1/sprints'

export function createSprintsApi(client: ApiClient) {
  return {
    list: (projectId: string) => client.get<Sprint[]>(`${BASE}?project_id=${projectId}`),
    /**
     * 지금 도는 스프린트 중 **내 일이 들어 있는 것.** 첫 화면이 쓴다.
     *
     * "도는 것 전부" 가 아니다 — 팀이 열이면 도는 스프린트도 열이고, 그중
     * 몇을 골라 보여 주는 것은 "내 일" 이 아니라 임의의 목록이다.
     */
    mine: (limit?: number) =>
      client.get<ActiveSprint[]>(
        limit === undefined ? `${BASE}/mine` : `${BASE}/mine?limit=${String(limit)}`,
      ),
    create: (body: NewSprint) => client.post<Sprint>(BASE, body),
    update: (id: string, body: SprintPatch) => client.patch<Sprint>(`${BASE}/${id}`, body),
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),
    start: (id: string) => client.post<Sprint>(`${BASE}/${id}/start`),
    /**
     * 닫는다. **남은 것을 어디로 보낼지 반드시 말해야 한다** — 기본값을 두면
     * "다음에 하기로 했던 것" 이 조용히 백로그로 간다.
     */
    close: (id: string, body: { move_to: string } | { to_backlog: true }) =>
      client.post<Sprint>(`${BASE}/${id}/close`, body),
    burndown: (id: string) => client.get<BurndownPoint[]>(`${BASE}/${id}/burndown`),
    /** 이슈를 넣거나(`sprint_id`) 백로그로 뺀다(`null`). */
    assign: (body: { project_id: string; sprint_id: string | null; issue_ids: string[] }) =>
      client.post<{ moved: number }>(`${BASE}/issues`, body),
  }
}

export type SprintsApi = ReturnType<typeof createSprintsApi>
