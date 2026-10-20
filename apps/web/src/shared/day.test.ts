/**
 * 날짜만 있는 값이 **하루 밀리지 않는가.**
 *
 * 시험은 뉴욕 시간대로 돈다(`vite.config.ts`). UTC 에서 돌면 "로컬의 오늘" 과
 * "UTC 의 오늘" 이 언제나 같아서, 둘을 혼동한 코드가 그냥 통과한다 — 이
 * 파일의 시험들은 UTC 에서는 하나도 안 붉어진다.
 */

import { describe, expect, it } from 'vitest'

import { dayFormatter, isoDay, isoDayFrom } from './day'

describe('isoDay', () => {
  it('늦은 밤에도 로컬 달력의 날이다', () => {
    // 뉴욕 10월 20일 21:00 은 UTC 로 10월 21일이다. `toISOString` 으로 만들던
    // 화면들은 이때부터 "오늘" 에 내일을 적었다.
    const lateNight = new Date(2026, 9, 20, 21, 0)
    expect(lateNight.toISOString().slice(0, 10)).toBe('2026-10-21')
    expect(isoDay(lateNight)).toBe('2026-10-20')
  })

  it('이른 아침에도 로컬 달력의 날이다', () => {
    expect(isoDay(new Date(2026, 9, 20, 0, 5))).toBe('2026-10-20')
  })
})

describe('isoDayFrom', () => {
  it('달력의 날을 센다', () => {
    expect(isoDayFrom(new Date(2026, 9, 20, 12), -7)).toBe('2026-10-13')
    expect(isoDayFrom(new Date(2026, 0, 3, 12), -7)).toBe('2025-12-27')
    expect(isoDayFrom(new Date(2026, 9, 20, 12), 0)).toBe('2026-10-20')
  })

  it('일광절약시간이 바뀌는 날을 건너도 하루는 하루다', () => {
    // 뉴욕은 2026-11-01 에 한 시간을 돌린다. 24시간 × 날 수로 빼면 자정 근처
    // 에서 하루가 덜 간다.
    expect(isoDayFrom(new Date(2026, 10, 2, 0, 30), -1)).toBe('2026-11-01')
    expect(isoDayFrom(new Date(2026, 10, 2, 0, 30), -2)).toBe('2026-10-31')
    // 봄(3월 8일)에는 하루가 23시간이다.
    expect(isoDayFrom(new Date(2026, 2, 8, 23, 30), 1)).toBe('2026-03-09')
  })
})

describe('dayFormatter', () => {
  it('그 날짜를 그대로 쓴다 — UTC 뒤의 시간대에서도 전날이 되지 않는다', () => {
    const format = dayFormatter('en-US', { month: 'numeric', day: 'numeric' })
    // 로컬로 쓰면 UTC 자정 = 뉴욕 전날 저녁이라 "9/30" 이 나왔다.
    expect(format('2026-10-01')).toBe('10/1')
  })

  it('달 이름도 그 달이다', () => {
    // 간트·달력의 제목이 이것이다. 10월을 보면서 "September" 가 적혔다.
    expect(dayFormatter('en-US', { month: 'long' })('2026-10-01')).toBe('October')
  })

  it('읽을 수 없는 값은 빈 글자다', () => {
    expect(dayFormatter('en-US', { dateStyle: 'medium' })('아무거나')).toBe('')
  })
})
