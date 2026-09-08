/**
 * 간트 (A17, M5).
 *
 * 달력이 "언제인가" 라면 간트는 **"무엇 다음인가"** 다.
 */

import type { ApiClient } from './client'
import type { CalendarEntry, CalendarSprint } from './calendar'

/** 막대 하나. 달력 칸과 **같은 모양**에 의존이 붙는다. */
export interface GanttRow extends CalendarEntry {
  /** 이 이슈보다 먼저 와야 하는 것들. **창 안에 있는 것만** 온다. */
  depends_on: string[]
}

/** `predecessor precedes successor` 인데 날짜가 그 순서를 안 지킨다. */
export interface GanttConflict {
  predecessor: string
  successor: string
  /** 며칠 어긋났나. 1 이면 후행이 선행이 끝나는 날에 시작한다. */
  overlap_days: number
}

export interface GanttWindow {
  starts_on: string
  ends_on: string
  /** **먼저 시작하는 것부터** 온다. */
  rows: GanttRow[]
  sprints: CalendarSprint[]
  /**
   * **어긋난 의존.** 비어 있지 않으면 화면은 눈에 띄게 적어야 한다 — 겹친
   * 막대만 그려 놓으면 사람은 화살표가 있으니 순서가 지켜진다고 읽는다.
   */
  conflicts: GanttConflict[]
  /** 날짜가 없어 막대를 그릴 수 없는 이슈 수. */
  undated: number
  truncated: boolean
  /**
   * 창 밖을 가리켜 그릴 수 없는 의존 수.
   *
   * 권한을 확인하지 않은 이슈의 일정을 읽지 않기 위해 그리지 않는다 —
   * 창을 넓혀 보면 그 이슈도 ACL 을 타고 들어온다.
   */
  links_outside: number
}

const BASE = '/api/v1/gantt'

export function createGanttApi(client: ApiClient) {
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
      return client.get<GanttWindow>(`${BASE}?${query.toString()}`)
    },
  }
}

export type GanttApi = ReturnType<typeof createGanttApi>
