/**
 * 목록 그룹화.
 *
 * **지금 화면에 있는 행만** 나눈다. 서버가 그룹별로 세어 주지 않으므로
 * 그룹 머리글의 숫자는 "이 페이지에 이만큼" 이라는 뜻이다 — 전체 개수인
 * 척하면 안 된다. 화면이 그렇게 말한다.
 *
 * 서버 그룹화는 그룹별 페이지네이션까지 같이 와야 의미가 있어서 M1 범위
 * 밖이다(보드 스윔레인도 같은 이유로 받아 온 카드를 나눈다).
 */

import type { IssueSummary } from '@ieum/api-client'

export const GROUP_FIELDS = ['none', 'status', 'assignee', 'priority', 'project'] as const
export type GroupField = (typeof GROUP_FIELDS)[number]

export function isGroupField(value: string): value is GroupField {
  return (GROUP_FIELDS as readonly string[]).includes(value)
}

export interface IssueGroup {
  /** 안정 식별자. 비어 있으면 "값 없음"(담당자 없음 등). */
  key: string
  /** 서버가 준 이름. 없으면 화면이 키로 만든다. */
  label: string
  rows: IssueSummary[]
}

/**
 * 행을 그룹으로 나눈다. 원래 순서를 유지한다 — 정렬은 서버가 정했고
 * 그룹화가 그걸 뒤집으면 "업데이트순" 이 깨진다.
 */
export function groupRows(rows: IssueSummary[], field: GroupField): IssueGroup[] {
  if (field === 'none') return [{ key: '', label: '', rows }]

  const groups = new Map<string, IssueGroup>()
  for (const row of rows) {
    const { key, label } = describe(row, field)
    const existing = groups.get(key)
    if (existing) existing.rows.push(row)
    else groups.set(key, { key, label, rows: [row] })
  }
  return order([...groups.values()], field)
}

function describe(row: IssueSummary, field: GroupField): { key: string; label: string } {
  switch (field) {
    case 'status':
      return { key: row.state_id, label: row.state_name }
    case 'assignee':
      return { key: row.assignee_id ?? '', label: '' }
    case 'priority':
      return { key: String(row.priority), label: '' }
    case 'project':
      // 프로젝트 이름은 행에 없다. 키의 앞부분이 프로젝트 키다(`ENG-12`).
      return { key: row.key.split('-')[0] ?? '', label: row.key.split('-')[0] ?? '' }
    default:
      return { key: '', label: '' }
  }
}

function order(groups: IssueGroup[], field: GroupField): IssueGroup[] {
  if (field === 'priority') {
    // 1 이 가장 높다. 급한 것이 위로 온다.
    return groups.sort((a, b) => Number(a.key) - Number(b.key))
  }
  if (field === 'status') {
    // 상태는 서버가 준 순서(카테고리 → position)를 그대로 둔다.
    return groups
  }
  const named = groups.filter((g) => g.key !== '')
  named.sort((a, b) => (a.label || a.key).localeCompare(b.label || b.key))
  // 값 없음은 맨 아래. 보통 아직 아무도 안 본 일이다.
  return [...named, ...groups.filter((g) => g.key === '')]
}
