/**
 * 반복 이슈 — 스케줄로 이슈를 만든다 (A27, M5).
 */

import type { ApiClient } from './client'

/** 언제 도는가. 크론이 아니라 **좁은 어휘**다. */
export type Cadence = 'daily' | 'weekly' | 'monthly'

export interface RecurrenceSchedule {
  cadence: Cadence
  /** 지역 시각(시·분). UTC 가 아니다. */
  hour: number
  minute: number
  /** **스케줄의 시간대다.** 보는 사람의 것이 아니다. */
  timezone: string
  /** 0=월 … 6=일. 매주일 때만. */
  weekday?: number | null
  /** 1~31. 매월일 때만. 짧은 달에서는 서버가 당긴다. */
  day?: number | null
}

export interface Recurrence {
  id: string
  project_id: string
  name: string
  summary: string
  description: string | null
  type_id: string | null
  assignee_id: string | null
  priority: number
  labels: string[]
  due_in_days: number | null

  cadence: string
  hour: number
  minute: number
  weekday: number | null
  day: number | null
  timezone: string

  /** 다음에 도는 시각. **화면이 이것을 보여 줘야** 켰는데 안 도는 것을 안다. */
  next_run_at: string
  last_run_at: string | null
  last_issue_id: string | null
  is_enabled: boolean
  /** 스스로 껐으면 그 **에러 코드**. 사람이 끈 것은 `null` (서버 주석 참조). */
  last_error: string | null
}

export interface RecurrenceInput {
  project_id: string
  name: string
  summary: string
  schedule: RecurrenceSchedule
  description?: string | null
  type_id?: string | null
  assignee_id?: string | null
  priority?: number
  labels?: string[]
  due_in_days?: number | null
}

const BASE = '/api/v1/recurrences'

export function createRecurrencesApi(client: ApiClient) {
  return {
    list: (projectId: string) =>
      client.get<Recurrence[]>(`${BASE}?project_id=${encodeURIComponent(projectId)}`),
    create: (body: RecurrenceInput) => client.post<Recurrence>(BASE, body),
    /** 전부 다시 보낸다 — 틀과 주기가 한 덩어리다(서버 주석 참조). */
    update: (id: string, body: RecurrenceInput) =>
      client.put<Recurrence>(`${BASE}/${id}`, body),
    setEnabled: (id: string, isEnabled: boolean) =>
      client.post<Recurrence>(`${BASE}/${id}/enabled`, { is_enabled: isEnabled }),
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),
  }
}

export type RecurrencesApi = ReturnType<typeof createRecurrencesApi>
