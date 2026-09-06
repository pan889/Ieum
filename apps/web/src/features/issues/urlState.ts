/**
 * 목록 화면 상태 ↔ URL 질의 문자열.
 *
 * 링크 하나로 같은 목록을 볼 수 있어야 한다(roadmap M1 완료 조건). 그래서
 * 필터는 컴포넌트 state 가 아니라 **URL 이 소유**한다 — 새로고침·뒤로가기·
 * 링크 공유가 전부 같은 경로로 동작한다.
 *
 * 칩은 **칩 상태 그대로** 싣는다(`project`, `status`, …). 생성된 IQL 만 싣고
 * 되파싱하면 클라이언트에 IQL 파서를 또 만들어야 하고 서버 문법과 갈라진다
 * (D-77). `iql` 은 사용자가 직접 쓴 질의이고, 있으면 그쪽이 이긴다.
 *
 * 컬럼 선택은 싣지 않는다. 그건 보는 사람의 취향이지 질의가 아니다 —
 * 링크를 받은 사람의 컬럼 설정까지 덮어쓰면 안 된다.
 */

import type { AssigneeFilter, IssueFilters } from './iql'
import { EMPTY_FILTERS } from './iql'

/** `/issues` 가 받는 질의 문자열. 전부 선택이다. */
export interface IssuesSearch {
  /** 직접 쓴 IQL. 있으면 IQL 모드다. */
  iql?: string
  project?: string
  /** todo,in_progress,done */
  status?: string
  /** any(생략) | me | none | <uuid> */
  assignee?: string
  type?: string
  /** 1,2,3,4,5 */
  priority?: string
  text?: string
}

const CATEGORIES = new Set(['todo', 'in_progress', 'done'])

/** 알 수 없는 키는 버린다. 남의 링크에 붙은 추적 파라미터까지 들고 다니지 않는다. */
export function parseSearch(raw: Record<string, unknown>): IssuesSearch {
  const search: IssuesSearch = {}
  for (const key of ['iql', 'project', 'status', 'assignee', 'type', 'priority', 'text'] as const) {
    const value = raw[key]
    if (typeof value === 'string' && value !== '') search[key] = value
  }
  return search
}

function splitList(value: string | undefined): string[] {
  if (!value) return []
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

function joinList(values: string[]): string | undefined {
  return values.length > 0 ? values.join(',') : undefined
}

function parseAssignee(value: string | undefined): AssigneeFilter {
  if (!value || value === 'any') return 'any'
  if (value === 'me') return 'me'
  // URL 에서는 'none' 이 읽기 좋다. 내부 이름과 다른 건 여기서만 안다.
  if (value === 'none') return 'unassigned'
  return { userId: value }
}

function serializeAssignee(assignee: AssigneeFilter): string | undefined {
  if (assignee === 'any') return undefined
  if (assignee === 'me') return 'me'
  if (assignee === 'unassigned') return 'none'
  return assignee.userId
}

/** URL → 칩 상태. 값이 이상하면 그 항목만 버린다(전체를 날리지 않는다). */
export function toFilters(search: IssuesSearch): IssueFilters {
  return {
    projectKey: search.project ?? null,
    statusCategories: splitList(search.status).filter((c) => CATEGORIES.has(c)),
    assignee: parseAssignee(search.assignee),
    typeNames: splitList(search.type),
    priorities: splitList(search.priority)
      .map(Number)
      // NaN 이 들어가면 IQL 에 `priority = NaN` 이 실려 서버가 거절한다.
      .filter((n) => Number.isInteger(n) && n >= 1 && n <= 5),
    text: search.text ?? '',
  }
}

/**
 * 칩 상태 → URL. 기본값인 항목은 아예 빼서 URL 을 짧게 유지한다.
 *
 * TanStack Router 는 `undefined` 인 키를 URL 에서 지운다. 빈 문자열을 넣으면
 * `?text=` 같은 껍데기가 남는다.
 */
export function toSearch(filters: IssueFilters): IssuesSearch {
  const search: IssuesSearch = {}
  if (filters.projectKey) search.project = filters.projectKey
  const status = joinList(filters.statusCategories)
  if (status) search.status = status
  const assignee = serializeAssignee(filters.assignee)
  if (assignee) search.assignee = assignee
  const types = joinList(filters.typeNames)
  if (types) search.type = types
  const priority = joinList(filters.priorities.map(String))
  if (priority) search.priority = priority
  const text = filters.text.trim()
  if (text) search.text = text
  return search
}

/**
 * IQL 모드로 전환한 URL.
 *
 * `filters` 를 주면 칩 파라미터를 **함께 남긴다.** 질의는 `iql` 이 이기고, 칩은
 * "칩으로 돌아가기" 가 무엇으로 돌아갈지 기억하려고 싣는다 — 안 실으면 방금
 * 칩에서 만든 질의인데도 돌아갈 곳이 없어진다. 손으로 고쳐서 둘이 어긋나면
 * `matchesChips` 가 알아서 되돌리기를 막는다(D-77).
 *
 * 저장 필터를 불러올 때처럼 칩 맥락이 없으면 `iql` 만 남는다. 그때는 되돌릴
 * 칩이 실제로 없는 게 맞다.
 */
export function toIqlSearch(iql: string, filters?: IssueFilters): IssuesSearch {
  if (iql.trim() === '') return filters ? toSearch(filters) : {}
  return { ...(filters ? toSearch(filters) : {}), iql }
}

/** 이 URL 이 IQL 모드인가. `iql` 이 있으면 칩 파라미터가 있어도 IQL 이 이긴다. */
export function isIqlMode(search: IssuesSearch): boolean {
  return typeof search.iql === 'string' && search.iql !== ''
}

/** 아무 필터도 없는 기본 화면인가. */
export function isBlank(search: IssuesSearch): boolean {
  return Object.keys(parseSearch(search as Record<string, unknown>)).length === 0
}

export { EMPTY_FILTERS }
