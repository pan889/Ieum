import { describe, expect, it } from 'vitest'

import type { IqlSuggestion } from '@ieum/api-client'

import { applySuggestion, moveActive, samePlace } from './iqlComplete'

function item(partial: Partial<IqlSuggestion>): IqlSuggestion {
  return { label: 'x', insert: 'x', kind: 'value', detail: '', caret: 1, ...partial }
}

describe('applySuggestion', () => {
  it('서버가 준 범위만 갈아 끼운다', () => {
    const applied = applySuggestion(
      'pro',
      { offset: 0, length: 3 },
      item({ label: 'project', insert: 'project ', caret: 8 }),
    )
    expect(applied).toEqual({ text: 'project ', caret: 8 })
  })

  it('커서 뒤 글자를 지우지 않는다', () => {
    const applied = applySuggestion(
      'pro = "ENG"',
      { offset: 0, length: 3 },
      item({ insert: 'project ', caret: 8 }),
    )
    expect(applied.text).toBe('project  = "ENG"')
  })

  it('열어 둔 따옴표를 겹쳐 넣지 않는다', () => {
    // 서버가 여는 따옴표부터 닫는 따옴표까지를 범위로 준다.
    const applied = applySuggestion(
      'project = "EN"',
      { offset: 10, length: 4 },
      item({ label: 'ENG', insert: '"ENG" ', caret: 6 }),
    )
    expect(applied.text).toBe('project = "ENG" ')
    expect(applied.caret).toBe(16)
  })

  it('보이는 글자와 넣는 글자가 다를 수 있다', () => {
    // 담당자는 UUID 로 컴파일된다. 목록에는 이름이 보인다.
    const applied = applySuggestion(
      'assignee = ',
      { offset: 11, length: 0 },
      item({ label: '홍길동', insert: '"01a0-…" ', caret: 9 }),
    )
    expect(applied.text).toBe('assignee = "01a0-…" ')
  })

  it('함수는 괄호 안에서 멈춘다', () => {
    const applied = applySuggestion(
      'created > ',
      { offset: 10, length: 0 },
      item({ label: 'startOfDay()', insert: 'startOfDay()', kind: 'function', caret: 11 }),
    )
    expect(applied.text).toBe('created > startOfDay()')
    expect(applied.caret).toBe(21)
  })
})

describe('moveActive', () => {
  it('끝에서 반대편으로 돈다', () => {
    // 눌리는데 아무 일도 안 나는 키는 고장으로 읽힌다.
    expect(moveActive(2, 1, 3)).toBe(0)
    expect(moveActive(0, -1, 3)).toBe(2)
  })

  it('빈 목록에서도 터지지 않는다', () => {
    expect(moveActive(0, 1, 0)).toBe(0)
  })
})

describe('samePlace', () => {
  it('같은 자리면 다시 묻지 않는다', () => {
    expect(samePlace({ iql: 'a', offset: 1 }, { iql: 'a', offset: 1 })).toBe(true)
    expect(samePlace({ iql: 'a', offset: 1 }, { iql: 'a', offset: 2 })).toBe(false)
    expect(samePlace(null, null)).toBe(true)
    expect(samePlace(null, { iql: 'a', offset: 1 })).toBe(false)
  })
})
