/**
 * 기한이 **화면에서 하루 앞당겨지지 않는가.**
 *
 * `formatDate` 는 시각(`created_at`)과 날짜만 있는 값(`due_date`·`work_date`)을
 * 함께 받는다. 날짜만 있는 값을 `new Date()` 로 읽으면 UTC 자정이고, 그것을
 * 로컬로 쓰면 뉴욕(시험의 시간대)에서는 전날이다 — 10월 1일까지인 일이 9월
 * 30일까지로 보였다.
 */

import { describe, expect, it, vi } from 'vitest'

import { formatDate } from './format'

// 언어를 못 박는다. 앱 초기화(`initI18n`) 없이도 포맷이 같은 글자를 내야 한다.
vi.mock('@/shared/i18n', () => ({ i18next: { language: 'en-US', t: () => '' } }))

describe('formatDate', () => {
  it('날짜만 있는 값은 그 날짜 그대로다', () => {
    expect(formatDate('2026-10-01')).toBe('Oct 1, 2026')
    expect(formatDate('2026-01-01')).toBe('Jan 1, 2026')
  })

  it('시각이 있는 값은 보는 사람의 날짜다', () => {
    // 10월 2일 02:00 UTC 는 뉴욕에서 아직 10월 1일 저녁이다.
    expect(formatDate('2026-10-02T02:00:00Z')).toBe('Oct 1, 2026')
  })

  it('비었거나 읽을 수 없으면 빈 글자다', () => {
    expect(formatDate(null)).toBe('')
    expect(formatDate('')).toBe('')
    expect(formatDate('2026-13-45')).toBe('')
    expect(formatDate('아무거나')).toBe('')
  })
})
