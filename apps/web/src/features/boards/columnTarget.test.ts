import { describe, expect, it } from 'vitest'

import { columnTarget, stateFitsColumn } from './columnTarget'

const OPEN = { id: '1', name: 'Open', category: 'todo' }
const DOING = { id: '2', name: 'In Progress', category: 'in_progress' }

describe('columnTarget', () => {
  it('reads the equality form', () => {
    expect(columnTarget('statusCategory = todo')).toEqual({ kind: 'category', values: ['todo'] })
    expect(columnTarget('status = "In Progress"')).toEqual({
      kind: 'status',
      values: ['In Progress'],
    })
  })

  it('reads the IN form', () => {
    expect(columnTarget('statusCategory IN (todo, "in_progress")')).toEqual({
      kind: 'category',
      values: ['todo', 'in_progress'],
    })
  })

  it('is case insensitive on the field and operator', () => {
    expect(columnTarget('STATUSCATEGORY in (done)')).toEqual({ kind: 'category', values: ['done'] })
  })

  it('says "I do not know" rather than guessing', () => {
    // 자동 이동이 엉뚱한 상태로 보내는 것보다 사용자에게 묻는 편이 낫다.
    expect(columnTarget('')).toBeNull()
    expect(columnTarget('priority < 3')).toBeNull()
    expect(columnTarget('statusCategory = todo AND priority = 1')).toBeNull()
    expect(columnTarget('status = "quote\\"inside"')).toBeNull()
  })
})

describe('stateFitsColumn', () => {
  it('matches by category or by name', () => {
    expect(stateFitsColumn(columnTarget('statusCategory = todo'), OPEN)).toBe(true)
    expect(stateFitsColumn(columnTarget('statusCategory = todo'), DOING)).toBe(false)
    expect(stateFitsColumn(columnTarget('status = "In Progress"'), DOING)).toBe(true)
  })

  it('never matches when the column is unknown', () => {
    expect(stateFitsColumn(null, OPEN)).toBe(false)
    expect(stateFitsColumn(columnTarget('priority = 1'), OPEN)).toBe(false)
  })
})
