import { describe, expect, it } from 'vitest'

import { countDue, isCalm } from './counts'

const TODAY = '2026-09-09'

describe('countDue', () => {
  it('지난 것과 오늘인 것을 가른다', () => {
    /**
     * **이 시험이 이 함수의 이유다.** 오늘을 "지났다" 로 세면 사람은 이미
     * 늦은 줄 알고, 지난 것을 "오늘" 로 세면 늦은 일이 늦어 보이지 않는다.
     */
    const found = countDue(['2026-09-08', '2026-09-09', '2026-09-10'], TODAY)
    expect(found).toEqual({ overdue: 1, today: 1 })
  })

  it('기한 없는 것은 어느 쪽도 아니다', () => {
    // 오늘로 세면 첫 화면이 늘 급해 보이고, 사람은 곧 그 숫자를 안 믿는다.
    expect(countDue([null, null, ''], TODAY)).toEqual({ overdue: 0, today: 0 })
  })

  it('여러 개를 더한다', () => {
    const found = countDue(
      ['2026-09-01', '2026-09-08', '2026-09-09', '2026-09-09', null, '2026-12-01'],
      TODAY,
    )
    expect(found).toEqual({ overdue: 2, today: 2 })
  })

  it('셀 것이 없으면 조용하다', () => {
    expect(isCalm(countDue([null, '2026-12-01'], TODAY))).toBe(true)
    expect(isCalm(countDue(['2026-09-09'], TODAY))).toBe(false)
    expect(isCalm(countDue(['2026-01-01'], TODAY))).toBe(false)
  })
})
