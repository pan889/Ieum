/**
 * 필터 칩 ↔ IQL.
 *
 * 칩은 IQL 의 **부분집합**만 표현한다. 칩에서 IQL 로는 언제나 갈 수 있지만
 * 그 반대는 일반적으로 불가능하다 — IQL 을 되파싱하는 파서를 클라이언트에
 * 또 만들면 서버 문법과 갈라진다. 그래서 "칩으로 돌아가기" 는 사용자가
 * 생성된 질의를 손대지 않았을 때만 허용한다 (`matchesChips`).
 */

export type AssigneeFilter = 'any' | 'me' | 'unassigned' | { userId: string }

export interface IssueFilters {
  /** 프로젝트 키. null 이면 전체. */
  projectKey: string | null
  /** todo / in_progress / done */
  statusCategories: string[]
  assignee: AssigneeFilter
  typeNames: string[]
  priorities: number[]
  /** 제목 부분 일치. */
  text: string
}

export const EMPTY_FILTERS: IssueFilters = {
  projectKey: null,
  statusCategories: [],
  assignee: 'any',
  typeNames: [],
  priorities: [],
  text: '',
}

/** IQL 문자열 리터럴. 역슬래시를 먼저 이스케이프해야 한다. */
export function quote(value: string): string {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
}

function orGroup(field: string, values: string[]): string | null {
  if (values.length === 0) return null
  if (values.length === 1) return `${field} = ${quote(values[0] as string)}`
  return `${field} IN (${values.map(quote).join(', ')})`
}

export function toIql(filters: IssueFilters): string {
  const parts: string[] = []

  if (filters.projectKey) parts.push(`project = ${quote(filters.projectKey)}`)

  const status = orGroup('statusCategory', filters.statusCategories)
  if (status) parts.push(status)

  const types = orGroup('type', filters.typeNames)
  if (types) parts.push(types)

  if (filters.priorities.length === 1) {
    parts.push(`priority = ${String(filters.priorities[0])}`)
  } else if (filters.priorities.length > 1) {
    parts.push(`priority IN (${filters.priorities.map(String).join(', ')})`)
  }

  if (filters.assignee === 'me') {
    parts.push('assignee = currentUser()')
  } else if (filters.assignee === 'unassigned') {
    parts.push('assignee IS EMPTY')
  } else if (typeof filters.assignee === 'object') {
    parts.push(`assignee = ${quote(filters.assignee.userId)}`)
  }

  const text = filters.text.trim()
  if (text) parts.push(`summary ~ ${quote(text)}`)

  return parts.join(' AND ')
}

/** 사용자가 생성된 질의를 손대지 않았는지. 손댔으면 칩으로 못 돌아간다. */
export function matchesChips(iql: string, filters: IssueFilters): boolean {
  return iql.trim() === toIql(filters)
}

export function isEmptyFilters(filters: IssueFilters): boolean {
  return toIql(filters) === ''
}

export function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}
