/**
 * 기한의 급함 계산 (feature-map B12).
 *
 * 날짜 비교는 조용히 틀린다: 오늘이 기한인 것을 "지났다" 로 보이면 사람은
 * 이미 늦은 줄 알고, "남았다" 로 보이면 오늘 할 것을 내일로 미룬다. 화면만
 * 봐서는 어느 쪽인지 모르므로 값으로 못 박는다.
 */

import { describe, expect, it } from 'vitest'

import { daysBetween, isoDay, urgencyOf } from './due'

const TODAY = '2026-09-08'

describe('urgencyOf', () => {
  it('기한이 없으면 급함도 없다', () => {
    expect(urgencyOf(null, TODAY)).toBe('none')
    expect(urgencyOf('', TODAY)).toBe('none')
  })

  it('어제까지는 지났다', () => {
    expect(urgencyOf('2026-09-07', TODAY)).toBe('overdue')
    expect(urgencyOf('2025-01-01', TODAY)).toBe('overdue')
  })

  it('오늘은 지난 것이 아니다', () => {
    // **경계가 이 함수의 전부다.** 오늘을 `overdue` 로 보이면 사람은 이미
    // 늦었다고 읽고, 오늘 할 수 있는 것을 포기한다.
    expect(urgencyOf(TODAY, TODAY)).toBe('today')
  })

  it('사흘 안쪽은 곧이다', () => {
    expect(urgencyOf('2026-09-09', TODAY)).toBe('soon')
    expect(urgencyOf('2026-09-11', TODAY)).toBe('soon')
  })

  it('나흘 뒤부터는 나중이다', () => {
    expect(urgencyOf('2026-09-12', TODAY)).toBe('later')
  })

  it('달과 해를 넘어가도 센다', () => {
    expect(urgencyOf('2026-10-01', TODAY)).toBe('later')
    expect(urgencyOf('2026-12-31', '2026-12-30')).toBe('soon')
    expect(urgencyOf('2027-01-01', '2026-12-31')).toBe('soon')
  })
})

describe('daysBetween', () => {
  it('같은 날은 0 이다', () => {
    expect(daysBetween(TODAY, TODAY)).toBe(0)
  })

  it('달을 넘어가도 맞는다', () => {
    expect(daysBetween('2026-09-30', '2026-10-01')).toBe(1)
  })

  it('윤년의 2월을 지난다', () => {
    // 2028 은 윤년이다. 2월이 29일이므로 28→29 가 하루다.
    expect(daysBetween('2028-02-28', '2028-03-01')).toBe(2)
    expect(daysBetween('2027-02-28', '2027-03-01')).toBe(1)
  })

  it('일광절약시간 경계에서도 하루는 하루다', () => {
    // 로컬 자정으로 빼면 봄·가을에 23·25시간이 되어 나머지가 생긴다.
    // UTC 자정으로 빼므로 언제나 24시간의 배수다.
    expect(daysBetween('2026-03-07', '2026-03-09')).toBe(2)
    expect(daysBetween('2026-10-31', '2026-11-02')).toBe(2)
  })

  it('거꾸로면 음수다', () => {
    expect(daysBetween('2026-09-10', '2026-09-08')).toBe(-2)
  })

  it('읽을 수 없는 날짜는 0 으로 둔다 — 던지지 않는다', () => {
    // 서버가 준 값이 이상해도 목록 하나가 통째로 안 그려지는 것보다 낫다.
    expect(daysBetween('아무거나', TODAY)).toBe(0)
  })
})

describe('isoDay', () => {
  it('로컬 달력의 그 날이다 — 이른 아침도 늦은 밤도', () => {
    /**
     * **UTC 가 아니라 사람이 보는 오늘이다.**
     *
     * 두 시각을 다 보는 이유: 하루의 어느 끝이 UTC 와 갈라지는지는 시간대가
     * UTC 보다 앞인지 뒤인지에 달렸다. 이른 아침만 보면 UTC 뒤(아메리카)에서
     * 통과하고, 늦은 밤만 보면 UTC 앞(아시아)에서 통과한다 — 한쪽만 보면
     * 그 CI 에서만 잡힌다.
     *
     * UTC 로 돌리면 둘 다 같은 답이라 이 시험은 아무것도 안 본다. 그건
     * 어쩔 수 없다: 시간대 문제는 시간대가 있어야 보인다.
     */
    expect(isoDay(new Date(2026, 8, 8, 1, 30))).toBe('2026-09-08')
    expect(isoDay(new Date(2026, 8, 8, 22, 30))).toBe('2026-09-08')
  })

  it('한 자리 달과 날에 0 을 붙인다', () => {
    expect(isoDay(new Date(2026, 0, 5))).toBe('2026-01-05')
  })
})
