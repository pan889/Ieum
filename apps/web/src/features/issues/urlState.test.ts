import { describe, expect, it } from 'vitest'

import type { IssueFilters } from './iql'
import { EMPTY_FILTERS, toIql } from './iql'
import { isBlank, isIqlMode, parseSearch, toFilters, toIqlSearch, toSearch } from './urlState'

describe('parseSearch', () => {
  it('모르는 키는 버린다', () => {
    expect(parseSearch({ project: 'ENG', utm_source: 'slack' })).toEqual({ project: 'ENG' })
  })

  it('빈 문자열은 없는 것으로 본다', () => {
    expect(parseSearch({ text: '', project: 'ENG' })).toEqual({ project: 'ENG' })
  })

  it('문자열이 아닌 값은 버린다', () => {
    expect(parseSearch({ priority: 3, project: 'ENG' })).toEqual({ project: 'ENG' })
  })
})

describe('칩 ↔ URL 왕복', () => {
  const filters: IssueFilters = {
    projectKey: 'ENG',
    statusCategories: ['todo', 'in_progress'],
    assignee: 'me',
    typeNames: ['Bug', 'Task'],
    priorities: [1, 2],
    text: 'login',
  }

  it('왕복해도 같다', () => {
    expect(toFilters(toSearch(filters))).toEqual(filters)
  })

  it('빈 필터는 빈 URL 이다', () => {
    expect(toSearch(EMPTY_FILTERS)).toEqual({})
    expect(toFilters({})).toEqual(EMPTY_FILTERS)
  })

  it('담당자 없음은 URL 에서 none 이다', () => {
    expect(toSearch({ ...EMPTY_FILTERS, assignee: 'unassigned' })).toEqual({ assignee: 'none' })
    expect(toFilters({ assignee: 'none' }).assignee).toBe('unassigned')
  })

  it('특정 사용자는 id 를 그대로 싣는다', () => {
    const userId = '01a07698-b5d7-7f85-860c-a20828993fa2'
    expect(toSearch({ ...EMPTY_FILTERS, assignee: { userId } })).toEqual({ assignee: userId })
    expect(toFilters({ assignee: userId }).assignee).toEqual({ userId })
  })

  it('공백만 있는 검색어는 싣지 않는다', () => {
    expect(toSearch({ ...EMPTY_FILTERS, text: '   ' })).toEqual({})
  })
})

describe('망가진 URL 을 견딘다', () => {
  it('모르는 상태 분류는 그 항목만 버린다', () => {
    expect(toFilters({ status: 'todo,nope,done' }).statusCategories).toEqual(['todo', 'done'])
  })

  it('숫자가 아닌 우선순위는 버린다', () => {
    // 남기면 IQL 에 `priority = NaN` 이 실려 서버가 질의를 통째로 거절한다.
    expect(toFilters({ priority: 'high,2' }).priorities).toEqual([2])
  })

  it('범위 밖 우선순위는 버린다', () => {
    expect(toFilters({ priority: '0,3,9' }).priorities).toEqual([3])
  })

  it('한 항목이 깨져도 나머지 필터는 산다', () => {
    const filters = toFilters({ project: 'ENG', status: 'garbage', text: 'x' })
    expect(filters.projectKey).toBe('ENG')
    expect(filters.text).toBe('x')
  })

  it('망가진 값에서 만든 IQL 도 유효한 모양이다', () => {
    expect(toIql(toFilters({ priority: 'high' }))).toBe('')
  })
})

describe('IQL 모드', () => {
  it('iql 이 있으면 IQL 모드다', () => {
    expect(isIqlMode({ iql: 'project = "ENG"' })).toBe(true)
    expect(isIqlMode({ project: 'ENG' })).toBe(false)
  })

  it('빈 iql 은 IQL 모드가 아니다', () => {
    expect(isIqlMode({ iql: '' })).toBe(false)
  })

  it('IQL 로 전환하면 칩 파라미터는 남기지 않는다', () => {
    // 둘 다 남으면 링크를 받은 사람이 어느 쪽이 진실인지 알 수 없다.
    expect(toIqlSearch('project = "ENG"')).toEqual({ iql: 'project = "ENG"' })
  })

  it('빈 질의는 아무것도 안 싣는다', () => {
    expect(toIqlSearch('   ')).toEqual({})
  })
})

describe('isBlank', () => {
  it('빈 URL 을 알아본다', () => {
    expect(isBlank({})).toBe(true)
    expect(isBlank({ iql: '' })).toBe(true)
    expect(isBlank({ project: 'ENG' })).toBe(false)
  })
})

describe('IQL 로 전환할 때 칩을 기억한다', () => {
  const filters: IssueFilters = { ...EMPTY_FILTERS, projectKey: 'ENG', statusCategories: ['todo'] }

  it('칩 파라미터를 함께 남긴다', () => {
    // 안 남기면 방금 칩에서 만든 질의인데도 돌아갈 곳이 없다.
    expect(toIqlSearch(toIql(filters), filters)).toEqual({
      project: 'ENG',
      status: 'todo',
      iql: 'project = "ENG" AND statusCategory = "todo"',
    })
  })

  it('되돌아온 칩이 원래 칩과 같다', () => {
    expect(toFilters(toIqlSearch(toIql(filters), filters))).toEqual(filters)
  })

  it('칩 맥락이 없으면 iql 만 남는다', () => {
    // 저장 필터를 불러온 경우. 되돌릴 칩이 실제로 없다.
    expect(toIqlSearch('project = "ENG"')).toEqual({ iql: 'project = "ENG"' })
  })

  it('질의를 비우면 칩 모드로 돌아간다', () => {
    expect(toIqlSearch('', filters)).toEqual({ project: 'ENG', status: 'todo' })
  })
})
