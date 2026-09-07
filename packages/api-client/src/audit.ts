import type { ApiClient } from './client'

/** 감사 로그 한 줄. `metadata` 는 행동마다 모양이 다르다. */
export interface AuditEntry {
  id: string
  action: string
  actor_id: string | null
  /** 행위자 이메일. id 만으로는 화면에서 사람을 못 알아본다. */
  actor_email: string | null
  target_type: string | null
  target_id: string | null
  ip: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface AuditPage {
  items: AuditEntry[]
  next_cursor: string | null
}

export interface AuditQuery {
  actor_id?: string
  /** 정확히 일치하거나(`auth.login.failed`), 점으로 끝나면 그 영역 전체(`auth.`). */
  action?: string
  target_type?: string
  target_id?: string
  /** ISO 8601. 포함(>=). */
  since?: string
  /** ISO 8601. 미포함(<). */
  until?: string
  cursor?: string
  limit?: number
}

const BASE = '/api/v1/audit'

function params(query: AuditQuery): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  return search.size > 0 ? `?${search.toString()}` : ''
}

export function createAuditApi(client: ApiClient) {
  return {
    list: (query: AuditQuery = {}) => client.get<AuditPage>(`${BASE}${params(query)}`),

    /** 필터에 쓸 행동 목록. 서버 상수에서 온다 — 아직 한 번도 안 일어난
     *  행동도 들어 있다. */
    actions: () => client.get<string[]>(`${BASE}/actions`),

    /** 같은 필터로 CSV. 감사 대응은 화면이 아니라 파일로 끝난다. */
    exportCsv: (query: AuditQuery = {}) => {
      const { cursor: _cursor, limit: _limit, ...rest } = query
      return client.getBlob(`${BASE}/export${params(rest)}`)
    },
  }
}

export type AuditApi = ReturnType<typeof createAuditApi>
