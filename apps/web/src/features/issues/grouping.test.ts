import { describe, expect, it } from 'vitest'

import type { IssueSummary } from '@ieum/api-client'

import { groupRows, isGroupField } from './grouping'

function row(over: Partial<IssueSummary> = {}): IssueSummary {
  return {
    id: 'i1',
    key: 'ENG-1',
    key_seq: 1,
    project_id: 'p1',
    summary: 'row',
    state_id: 's1',
    state_name: 'Open',
    state_category: 'todo',
    assignee_id: null,
    priority: 3,
    due_date: null,
    updated_at: '2026-09-06T00:00:00Z',
    ...over,
  }
}

describe('groupRows', () => {
  it('none 이면 한 덩어리다', () => {
    const rows = [row({ id: 'a' }), row({ id: 'b' })]
    expect(groupRows(rows, 'none')).toEqual([{ key: '', label: '', rows }])
  })

  it('원래 순서를 유지한다', () => {
    // 정렬은 서버가 정했다. 그룹화가 뒤집으면 "업데이트순" 이 깨진다.
    const rows = [
      row({ id: 'a', state_id: 's1', state_name: 'Open' }),
      row({ id: 'b', state_id: 's2', state_name: 'Done' }),
      row({ id: 'c', state_id: 's1', state_name: 'Open' }),
    ]
    const groups = groupRows(rows, 'status')
    expect(groups.map((g) => g.label)).toEqual(['Open', 'Done'])
    expect(groups[0]?.rows.map((r) => r.id)).toEqual(['a', 'c'])
  })

  it('우선순위는 높은 것이 위다', () => {
    const rows = [row({ id: 'low', priority: 5 }), row({ id: 'high', priority: 1 })]
    expect(groupRows(rows, 'priority').map((g) => g.key)).toEqual(['1', '5'])
  })

  it('담당자 없음은 맨 아래다', () => {
    const rows = [
      row({ id: 'none', assignee_id: null }),
      row({ id: 'someone', assignee_id: 'u1' }),
    ]
    expect(groupRows(rows, 'assignee').map((g) => g.key)).toEqual(['u1', ''])
  })

  it('프로젝트는 이슈 키 앞부분으로 나눈다', () => {
    // 행에 프로젝트 이름이 없다. `ENG-12` 의 앞부분이 프로젝트 키다.
    const rows = [row({ id: 'a', key: 'ENG-1' }), row({ id: 'b', key: 'MKT-9' })]
    expect(groupRows(rows, 'project').map((g) => g.key)).toEqual(['ENG', 'MKT'])
  })

  it('행이 없으면 그룹도 없다', () => {
    expect(groupRows([], 'status')).toEqual([])
  })

  it('빈 그룹은 만들지 않는다', () => {
    const rows = [row({ id: 'a', priority: 2 })]
    expect(groupRows(rows, 'priority')).toHaveLength(1)
  })
})

describe('isGroupField', () => {
  it('아는 값만 통과시킨다', () => {
    expect(isGroupField('status')).toBe(true)
    expect(isGroupField('epic')).toBe(false)
  })
})
