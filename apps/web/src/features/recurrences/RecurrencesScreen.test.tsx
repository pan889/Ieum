import { describe, expect, it } from 'vitest'

import type { Recurrence } from '@ieum/api-client'

import { describeSchedule, type Translate } from './RecurrencesScreen'

/**
 * 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 안 깨진다.
 *
 * 넘긴 값을 대괄호로 붙여 준다 — 요일·날짜가 문장에 실제로 실렸는지 봐야
 * 하는데, 키만 돌려주면 그것이 보이지 않는다.
 */
const t: Translate = (key, options) =>
  options === undefined ? key : `${key}[${Object.values(options).join(',')}]`

type Shape = Pick<Recurrence, 'cadence' | 'hour' | 'minute' | 'weekday' | 'day' | 'timezone'>

function row(over: Partial<Shape> = {}): Shape {
  return {
    cadence: 'daily',
    hour: 9,
    minute: 0,
    weekday: null,
    day: null,
    timezone: 'Asia/Seoul',
    ...over,
  }
}

describe('describeSchedule', () => {
  it('시간대를 함께 적는다', () => {
    /**
     * **이 시험이 이 함수의 이유다.** "매일 09:00" 만 적으면 보는 사람은
     * 자기 시간대의 9시로 읽는다 — 스케줄의 시간대가 다르면 그건 틀린 읽기고,
     * 화면은 그것을 바로잡을 기회가 여기밖에 없다.
     */
    expect(describeSchedule(row(), t)).toBe('recurrences:everyDay 09:00 (Asia/Seoul)')
  })

  it('시·분을 두 자리로 맞춘다', () => {
    // "9:0" 은 시각처럼 안 읽힌다.
    expect(describeSchedule(row({ hour: 9, minute: 5 }), t)).toContain('09:05')
    expect(describeSchedule(row({ hour: 23, minute: 30 }), t)).toContain('23:30')
    expect(describeSchedule(row({ hour: 0, minute: 0 }), t)).toContain('00:00')
  })

  it('매주면 요일을 말한다', () => {
    const found = describeSchedule(row({ cadence: 'weekly', weekday: 2 }), t)
    expect(found).toBe('recurrences:everyWeekday[recurrences:weekday.2] 09:00 (Asia/Seoul)')
  })

  it('매월이면 날짜를 말한다', () => {
    const found = describeSchedule(row({ cadence: 'monthly', day: 15 }), t)
    expect(found).toBe('recurrences:everyDayOfMonth[15] 09:00 (Asia/Seoul)')
  })
})
