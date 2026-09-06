import { describe, expect, it } from 'vitest'

import { applySuggestion, excerpt } from './iqlError'

describe('excerpt', () => {
  it('틀린 자리 아래에 캐럿을 깐다', () => {
    const { line, caret } = excerpt('project = "ENG" AND assigne = 1', { offset: 20, length: 7 })
    expect(line).toBe('project = "ENG" AND assigne = 1')
    expect(caret).toBe(' '.repeat(20) + '^'.repeat(7))
  })

  it('여러 줄이면 그 줄만 뽑는다', () => {
    const text = 'project = "ENG"\nAND assigne = 1'
    const { line, caret } = excerpt(text, { offset: 20, length: 7 })
    expect(line).toBe('AND assigne = 1')
    expect(caret).toBe('    ^^^^^^^')
  })

  it('긴 줄은 틀린 자리를 가운데 두고 창을 민다', () => {
    // 캐럿이 화면 밖에 있으면 없느니만 못하다.
    const text = 'a'.repeat(100) + ' AND assigne = 1'
    const { line, caret } = excerpt(text, { offset: 105, length: 7 }, 40)
    expect(line.length).toBeLessThanOrEqual(42) // 양끝 말줄임 포함
    expect(line.startsWith('…')).toBe(true)
    expect(caret.indexOf('^')).toBeGreaterThan(0)
    // 캐럿이 실제로 그 낱말 위에 있다.
    expect(line.slice(caret.indexOf('^'), caret.lastIndexOf('^') + 1)).toBe('assigne')
  })

  it('길이가 0 이어도 한 칸은 가리킨다', () => {
    expect(excerpt('project =', { offset: 9, length: 0 }).caret).toBe('         ^')
  })

  it('범위를 벗어난 위치에도 터지지 않는다', () => {
    expect(() => excerpt('short', { offset: 999, length: 5 })).not.toThrow()
    expect(() => excerpt('short', { offset: -3, length: 5 })).not.toThrow()
  })
})

describe('applySuggestion', () => {
  it('틀린 자리만 갈아 끼운다', () => {
    const result = applySuggestion('project = "ENG" AND assigne = 1', { offset: 20, length: 7 }, 'assignee')
    expect(result.text).toBe('project = "ENG" AND assignee = 1')
    expect(result.caret).toBe(28)
  })
})
