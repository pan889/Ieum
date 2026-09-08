/**
 * 달력 (A18, M5).
 *
 * 스프린트가 기간을 1급으로 만들었으니, 그 기간을 격자 위에 놓는다.
 */

import type { ApiClient } from './client'

export interface CalendarEntry {
  id: string
  key: string
  summary: string
  /** 놓이는 첫 날과 마지막 날 (`YYYY-MM-DD`). 하루짜리는 둘이 같다. */
  starts_on: string
  ends_on: string
  state_name: string
  state_category: string
  assignee_id: string | null
  priority: number
  /** 끝나기로 한 날이 지났고 아직 done 이 아니다. */
  overdue: boolean
}

/** 달력 위에 얹는 스프린트 띠. */
export interface CalendarSprint {
  id: string
  name: string
  state: string
  /** **시각 그대로** 온다 — 날짜로 접는 것은 보는 쪽 타임존의 일이다. */
  starts_at: string | null
  ends_at: string | null
}

export interface CalendarWindow {
  starts_on: string
  ends_on: string
  entries: CalendarEntry[]
  sprints: CalendarSprint[]
  /**
   * 질의에는 맞지만 **날짜가 없어서 놓을 수 없는** 이슈 수.
   *
   * 0 이 아니면 화면은 그것을 적어야 한다. 조용히 빼면 사람은 "이번 주는
   * 비어 있다" 고 읽는데, 실제로는 날짜만 안 적힌 일이 스무 건 있다.
   */
  undated: number
  /** 상한에서 잘렸나. */
  truncated: boolean
}

const BASE = '/api/v1/calendar'

export function createCalendarApi(client: ApiClient) {
  return {
    window: (params: {
      projectId: string
      startsOn: string
      endsOn: string
      iql?: string | undefined
    }) => {
      const query = new URLSearchParams({
        project_id: params.projectId,
        starts_on: params.startsOn,
        ends_on: params.endsOn,
      })
      if (params.iql) query.set('iql', params.iql)
      return client.get<CalendarWindow>(`${BASE}?${query.toString()}`)
    },
  }
}

export type CalendarApi = ReturnType<typeof createCalendarApi>
