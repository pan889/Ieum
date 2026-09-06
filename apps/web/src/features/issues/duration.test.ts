import { describe, expect, it } from 'vitest'

import { formatDuration, parseDuration } from './duration'

describe('parseDuration', () => {
  it('reads unit suffixes', () => {
    expect(parseDuration('30m')).toBe(30)
    expect(parseDuration('2h')).toBe(120)
    expect(parseDuration('1d')).toBe(480)
    expect(parseDuration('1w')).toBe(2400)
  })

  it('adds the parts together', () => {
    expect(parseDuration('1d 2h 30m')).toBe(630)
    expect(parseDuration('1h30m')).toBe(90)
  })

  it('is case insensitive', () => {
    expect(parseDuration('2H 15M')).toBe(135)
  })

  it('reads a bare number as minutes', () => {
    // 시간으로 보면 "30" 이 30시간이 되어 조용히 60배 틀린다.
    expect(parseDuration('30')).toBe(30)
  })

  it('rejects anything it cannot fully read', () => {
    // 부분 해석은 사용자가 의도한 값과 다른 값을 조용히 저장한다.
    expect(parseDuration('2h banana')).toBeNull()
    expect(parseDuration('two hours')).toBeNull()
    expect(parseDuration('2y')).toBeNull()
    expect(parseDuration('')).toBeNull()
    expect(parseDuration('0m')).toBeNull()
    expect(parseDuration('-1h')).toBeNull()
  })
})

describe('formatDuration', () => {
  it('is the inverse for round values', () => {
    for (const minutes of [1, 30, 60, 90, 480, 630, 2400, 2401]) {
      expect(parseDuration(formatDuration(minutes))).toBe(minutes)
    }
  })

  it('drops empty units', () => {
    expect(formatDuration(120)).toBe('2h')
    expect(formatDuration(480)).toBe('1d')
    expect(formatDuration(0)).toBe('0m')
  })
})
