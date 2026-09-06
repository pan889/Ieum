import type { ApiClient } from './client'

export interface IssueSummary {
  id: string
  key: string
  key_seq: number
  project_id: string
  summary: string
  state_id: string
  state_name: string
  state_category: string
  assignee_id: string | null
  priority: number
  due_date: string | null
  updated_at: string
}

export interface Issue {
  id: string
  key: string
  project_id: string
  summary: string
  description: string | null
  type_id: string
  type_name: string
  state_id: string
  state_name: string
  state_category: string
  reporter_id: string
  assignee_id: string | null
  priority: number
  parent_id: string | null
  due_date: string | null
  progress: number
  resolved_at: string | null
  archived_at: string | null
  version: number
  labels: string[]
  custom_fields: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface IssuePage {
  items: IssueSummary[]
  next_cursor: string | null
  total: number | null
}

export interface IssueType {
  id: string
  name: string
  icon: string | null
  is_subtask: boolean
  workflow_id: string
  position: number
}

export interface WorkflowStateInfo {
  id: string
  name: string
  category: string
  position: number
  is_initial: boolean
}

export interface FieldDefinition {
  id: string
  key: string
  name: string
  kind: string
  description: string | null
  config: Record<string, unknown>
  is_required: boolean
  position: number
}

export interface AvailableTransition {
  id: string
  name: string
  to_state_id: string
  to_state_name: string
  /** 비어 있으면 지금 실행할 수 있다. 차 있으면 통과 못 한 조건 이름이다. */
  blocked_by: string[]
}

export interface IssueComment {
  id: string
  issue_id: string
  author_id: string
  body: string
  is_internal: boolean
  edited_at: string | null
  created_at: string
}

export interface HistoryEntry {
  id: string
  actor_id: string | null
  changes: { field: string; from: unknown; to: unknown }[]
  created_at: string
}

export interface NewIssue {
  project_id: string
  summary: string
  type_id?: string
  description?: string
  assignee_id?: string | null
  priority?: number
  parent_id?: string | null
  due_date?: string | null
  labels?: string[]
  custom_fields?: Record<string, unknown>
}

/**
 * 부분 수정. 서버는 **미포함과 null 을 구분한다** — `changes` 에 키가
 * 없으면 건드리지 않고, null 이면 비운다. 그래서 필드를 최상위에 두지 않고
 * changes 안에 담는다.
 */
export interface IssueChanges {
  summary?: string
  description?: string | null
  assignee_id?: string | null
  priority?: number
  parent_id?: string | null
  category_id?: string | null
  fix_version_id?: string | null
  start_date?: string | null
  due_date?: string | null
  estimate_minutes?: number | null
  progress?: number
}

export interface IssuePatch {
  changes?: IssueChanges
  labels?: string[]
  custom_fields?: Record<string, unknown>
}

const BASE = '/api/v1/issues'

/** 낙관적 잠금 헤더. version 이 없으면 검사하지 않는다. */
function ifMatch(version?: number): { headers: Record<string, string> } | undefined {
  return version === undefined ? undefined : { headers: { 'If-Match': String(version) } }
}

export function createIssuesApi(client: ApiClient) {
  return {
    list: (params: {
      projectId?: string
      cursor?: string
      limit?: number
      includeArchived?: boolean
    } = {}) => {
      const query = new URLSearchParams()
      if (params.projectId) query.set('project_id', params.projectId)
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.includeArchived) query.set('include_archived', 'true')
      const suffix = query.size > 0 ? `?${query.toString()}` : ''
      return client.get<IssuePage>(`${BASE}${suffix}`)
    },

    get: (id: string) => client.get<Issue>(`${BASE}/${id}`),
    getByKey: (key: string) => client.get<Issue>(`${BASE}/by-key/${encodeURIComponent(key)}`),

    create: (body: NewIssue) => client.post<Issue>(BASE, body),

    update: (id: string, body: IssuePatch, version?: number) =>
      client.patch<Issue>(`${BASE}/${id}`, body, ifMatch(version)),

    /** 필드 몇 개만 고치는 흔한 경우. changes 로 감싸 준다. */
    change: (id: string, changes: IssueChanges, version?: number) =>
      client.patch<Issue>(`${BASE}/${id}`, { changes }, ifMatch(version)),

    archive: (id: string) => client.post<Issue>(`${BASE}/${id}/archive`),

    transitions: (id: string) => client.get<AvailableTransition[]>(`${BASE}/${id}/transitions`),

    transition: (id: string, transitionId: string, version?: number) =>
      client.post<Issue>(`${BASE}/${id}/transition`, { transition_id: transitionId }, ifMatch(version)),

    link: (id: string, body: { target_id: string; link_type: string }) =>
      client.post<void>(`${BASE}/${id}/links`, body),

    history: (id: string) => client.get<HistoryEntry[]>(`${BASE}/${id}/history`),

    comments: (id: string) => client.get<IssueComment[]>(`${BASE}/${id}/comments`),

    addComment: (id: string, body: { body: string; is_internal?: boolean }) =>
      client.post<IssueComment>(`${BASE}/${id}/comments`, body),

    editComment: (commentId: string, body: { body: string }) =>
      client.patch<IssueComment>(`${BASE}/comments/${commentId}`, body),

    // ── 메타. 생성 폼과 필터 칩이 쓴다 ──────────────────────────
    types: (projectId: string) =>
      client.get<IssueType[]>(`${BASE}/types?project_id=${projectId}`),

    states: (projectId: string) =>
      client.get<WorkflowStateInfo[]>(`${BASE}/states?project_id=${projectId}`),

    fields: (projectId: string, typeId: string) =>
      client.get<FieldDefinition[]>(`${BASE}/fields?project_id=${projectId}&type_id=${typeId}`),
  }
}

export type IssuesApi = ReturnType<typeof createIssuesApi>
