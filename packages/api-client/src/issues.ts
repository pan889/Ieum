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
  start_date: string | null
  due_date: string | null
  estimate_minutes: number | null
  progress: number
  /**
   * 어느 스프린트에 있나. `null` 이면 백로그다.
   *
   * **이름은 안 온다.** 목록·보드도 같은 스키마를 쓰고, 이름을 채우려면
   * 카드마다 조회가 하나씩 는다 — 보드는 한 번에 600장을 그린다. 이름이
   * 필요한 화면은 그 프로젝트의 스프린트 목록을 한 번 받아 맞춘다.
   */
  sprint_id: string | null
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

export interface Version {
  id: string
  name: string
  description: string | null
  start_date: string | null
  release_date: string | null
  status: string
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

export interface Worklog {
  id: string
  issue_id: string
  user_id: string | null
  spent_minutes: number
  work_date: string
  comment: string | null
  created_at: string
}

export interface TimeSummary {
  estimate_minutes: number | null
  spent_minutes: number
  remaining_minutes: number | null
  over_estimate: boolean
}

export interface WorklogPanel {
  summary: TimeSummary
  items: Worklog[]
}

export interface RelatedIssue {
  link_id: string
  kind: string
  /** True 면 이 이슈가 관계의 출발점이다 ("blocks" vs "blocked by"). */
  outward: boolean
  issue: IssueSummary
}

export interface IssueRelations {
  parent: IssueSummary | null
  children: IssueSummary[]
  links: RelatedIssue[]
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

export interface BulkFailure {
  issue_id: string
  code: string
  message: string
}

export interface BulkEditResult {
  updated: string[]
  failed: BulkFailure[]
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

    link: (id: string, body: { to_issue_id: string; kind: string }) =>
      client.post<void>(`${BASE}/${id}/links`, body),

    history: (id: string) => client.get<HistoryEntry[]>(`${BASE}/${id}/history`),

    comments: (id: string) => client.get<IssueComment[]>(`${BASE}/${id}/comments`),

    addComment: (id: string, body: { body: string; is_internal?: boolean }) =>
      client.post<IssueComment>(`${BASE}/${id}/comments`, body),

    editComment: (commentId: string, body: { body: string }) =>
      client.patch<IssueComment>(`${BASE}/comments/${commentId}`, body),

    /**
     * 일괄 편집. **부분 성공**을 돌려준다 — 200 이어도 failed 를 봐야 한다.
     */
    bulkEdit: (body: {
      issue_ids: string[]
      changes?: Record<string, unknown>
      add_labels?: string[]
      remove_labels?: string[]
      transition_id?: string
    }) => client.post<BulkEditResult>(`${BASE}/bulk`, body),

    // ── 관계 ──────────────────────────────────────────────────
    relations: (id: string) => client.get<IssueRelations>(`${BASE}/${id}/relations`),

    unlink: (linkId: string) => client.delete<void>(`${BASE}/links/${linkId}`),

    // ── 시간 추적 ─────────────────────────────────────────────
    worklogs: (id: string) => client.get<WorklogPanel>(`${BASE}/${id}/worklogs`),

    /** 분 단위 정수만 보낸다. "2h 30m" 파싱은 클라이언트 몫이다. */
    addWorklog: (
      id: string,
      body: { spent_minutes: number; work_date?: string; comment?: string },
    ) => client.post<Worklog>(`${BASE}/${id}/worklogs`, body),

    updateWorklog: (
      worklogId: string,
      body: {
        spent_minutes?: number
        work_date?: string
        comment?: string
        clear_comment?: boolean
      },
    ) => client.patch<Worklog>(`${BASE}/worklogs/${worklogId}`, body),

    deleteWorklog: (worklogId: string) => client.delete<void>(`${BASE}/worklogs/${worklogId}`),

    // ── 메타. 생성 폼과 필터 칩이 쓴다 ──────────────────────────
    types: (projectId: string) =>
      client.get<IssueType[]>(`${BASE}/types?project_id=${projectId}`),

    states: (projectId: string) =>
      client.get<WorkflowStateInfo[]>(`${BASE}/states?project_id=${projectId}`),

    fields: (projectId: string, typeId: string) =>
      client.get<FieldDefinition[]>(`${BASE}/fields?project_id=${projectId}&type_id=${typeId}`),

    versions: (projectId: string) =>
      client.get<Version[]>(`${BASE}/versions?project_id=${projectId}`),
  }
}

export type IssuesApi = ReturnType<typeof createIssuesApi>


// ── 관리 콘솔: 워크플로우와 필드 정의 ──────────────────────────

const ADMIN_WORKFLOWS = '/api/v1/admin/workflows'
const ADMIN_FIELDS = '/api/v1/admin/fields'

/** 목록 한 줄. 고칠 때 무엇이 영향을 받는지까지 담는다. */
export interface WorkflowSummary {
  id: string
  name: string
  description: string | null
  is_builtin: boolean
  state_count: number
  transition_count: number
  /** 이 워크플로우를 쓰는 이슈 유형 이름. */
  used_by: string[]
}

export interface WorkflowStateDetail {
  id: string
  name: string
  /** 보드 컬럼·"완료 여부" 판정의 근거. 이름이 뭐든 시스템은 이걸 본다. */
  category: string
  position: number
  is_initial: boolean
}

export interface WorkflowTransitionDetail {
  id: string
  name: string
  /** null 이면 **모든 상태에서** 갈 수 있다(전역 전이). */
  from_state_id: string | null
  to_state_id: string
  conditions: Record<string, unknown>[]
  post_functions: Record<string, unknown>[]
  position: number
}

export interface WorkflowDetail {
  id: string
  name: string
  description: string | null
  is_builtin: boolean
  states: WorkflowStateDetail[]
  transitions: WorkflowTransitionDetail[]
  used_by: string[]
}

/**
 * 워크플로우 조회. **편집은 없다** — 상태를 지우면 이미 그 상태에 있는
 * 이슈를 어디로 보낼지 정해야 한다(서버의 issues/admin.py 참고).
 */
export function createWorkflowsApi(client: ApiClient) {
  return {
    list: () => client.get<WorkflowSummary[]>(ADMIN_WORKFLOWS),
    get: (id: string) => client.get<WorkflowDetail>(`${ADMIN_WORKFLOWS}/${id}`),
  }
}

export type WorkflowsApi = ReturnType<typeof createWorkflowsApi>

/** 관리 화면용 필드 정의. 목록에 필요한 곁가지까지 담는다. */
export interface FieldDefinitionAdmin {
  id: string
  key: string
  name: string
  kind: string
  description: string | null
  config: Record<string, unknown>
  is_required: boolean
  position: number
  /** null 이면 제한 없음. 둘 다 null 이면 어디서나 보인다. */
  project_id: string | null
  issue_type_id: string | null
  /** 이 필드에 값을 넣은 이슈 수. **지우기 전에 알아야 한다.** */
  value_count: number
}

/**
 * 커스텀 필드 정의.
 *
 * 키와 종류는 **만들 때만** 정한다. 키는 IQL 식별자이자 값의 주소이고,
 * 종류는 값의 해석을 정한다 — 나중에 바꾸면 저장된 값이 어긋난다.
 */
export function createFieldsApi(client: ApiClient) {
  return {
    list: () => client.get<FieldDefinitionAdmin[]>(ADMIN_FIELDS),
    create: (body: {
      key: string
      name: string
      kind: string
      description?: string | null
      config?: Record<string, unknown>
      is_required?: boolean
      position?: number
    }) => client.post<FieldDefinition>(ADMIN_FIELDS, body),
    update: (
      id: string,
      body: {
        name?: string
        description?: string | null
        config?: Record<string, unknown>
        is_required?: boolean
        position?: number
      },
    ) => client.patch<FieldDefinition>(`${ADMIN_FIELDS}/${id}`, body),
    /** 정의와 **그 값**을 함께 지운다. 남기면 같은 키로 되살아난다. */
    remove: (id: string) => client.delete<void>(`${ADMIN_FIELDS}/${id}`),
  }
}

export type FieldsApi = ReturnType<typeof createFieldsApi>
