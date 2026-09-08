/**
 * 리포트 — IQL 이 고른 것을 센다 (A29, M5).
 */

import type { ApiClient } from './client'

export interface ReportBucket {
  /**
   * `null` 이면 **"값이 없는 것들"** 이다 — 빈 칸이 아니라 하나의 칸이다.
   * 화면은 "담당자 없음" 처럼 이름을 붙여야 한다.
   */
  key: string | null
  count: number
}

export interface CountReport {
  group_by: string
  buckets: ReportBucket[]
  /**
   * 조건에 맞는 이슈 수.
   *
   * **`multi_valued` 가 거짓이면 칸의 합과 같다.** 안 맞으면 리포트가
   * 거짓말을 하는 것이고, 그때는 이 값을 믿어야 한다.
   */
  total: number
  /** 이슈 하나가 여러 칸에 들어갈 수 있나(라벨). 그러면 합 > 총계다. */
  multi_valued: boolean
  /** 칸 상한에서 잘렸나. 잘렸으면 합이 총계보다 작다. */
  truncated: boolean
  /** 셀 수 있는 기준 전부. 화면이 목록을 손으로 들지 않게 한다. */
  supported: string[]
}

const BASE = '/api/v1/reports'

export function createReportsApi(client: ApiClient) {
  return {
    /** IQL 이 길면 URL 상한에 걸리므로 POST 다 — 검색과 같은 이유. */
    count: (body: { iql: string; group_by: string }) =>
      client.post<CountReport>(`${BASE}/count`, body),
  }
}

export type ReportsApi = ReturnType<typeof createReportsApi>
