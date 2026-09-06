import { describe, expect, it } from 'vitest'

import { applyMention, findMentionQuery } from './mention'

const ALICE = { id: '01a0-alice', display_name: 'Alice Kim' }

describe('findMentionQuery', () => {
  it('finds a mention at the caret', () => {
    expect(findMentionQuery('hi @al', 6)).toEqual({ start: 3, term: 'al' })
  })

  it('finds a bare @ at the start', () => {
    expect(findMentionQuery('@', 1)).toEqual({ start: 0, term: '' })
  })

  it('allows spaces so full names can be typed', () => {
    expect(findMentionQuery('cc @Alice K', 11)).toEqual({ start: 3, term: 'Alice K' })
  })

  it('ignores an @ inside a word', () => {
    // 이메일을 칠 때마다 목록이 뜨면 못 쓴다.
    expect(findMentionQuery('mail a@b.com', 12)).toBeNull()
  })

  it('gives up once the term runs long', () => {
    expect(findMentionQuery('@one two three four', 19)).toBeNull()
  })

  it('is null when there is no @ before the caret', () => {
    expect(findMentionQuery('plain text', 10)).toBeNull()
  })
})

describe('applyMention', () => {
  it('replaces the typed fragment with a markdown mention', () => {
    const result = applyMention('hi @al', { start: 3, term: 'al' }, 6, ALICE)
    expect(result.text).toBe('hi [@Alice Kim](user:01a0-alice)')
    expect(result.caret).toBe(result.text.length)
  })

  it('keeps whatever follows the caret', () => {
    const result = applyMention('hi @al rest', { start: 3, term: 'al' }, 6, ALICE)
    expect(result.text).toBe('hi [@Alice Kim](user:01a0-alice) rest')
  })
})
