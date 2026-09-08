import { describe, expect, it } from 'vitest'

import type { CalendarEntry } from '@ieum/api-client'

import { dayOf, isoOf, monthWindow, segmentIn, stackIn, weekdayOf, weeksOf } from './grid'

function entry(key: string, starts_on: string, ends_on: string): CalendarEntry {
  return {
    id: `id-${key}`,
    key,
    summary: key,
    starts_on,
    ends_on,
    state_name: 'Open',
    state_category: 'todo',
    assignee_id: null,
    priority: 3,
    overdue: false,
  }
}

/** 없으면 터진다. `undefined` 를 조용히 통과시키지 않으려는 것이다. */
function at<T>(items: readonly T[], index: number): T {
  const found = items[index]
  if (found === undefined) throw new Error(`${index} 번째 값이 없다`)
  return found
}

describe('날짜 셈', () => {
  it('문자열과 정수를 왕복한다', () => {
    expect(isoOf(dayOf('2026-09-15'))).toBe('2026-09-15')
  })

  it('요일을 안다 — 1970-01-01 은 목요일이었다', () => {
    expect(weekdayOf(dayOf('1970-01-01'))).toBe(4)
    expect(weekdayOf(dayOf('2026-09-13'))).toBe(0) // 일요일
  })
})

describe('monthWindow', () => {
  it('**주 경계까지 넓힌다** — 1일 앞의 며칠도 격자에 있다', () => {
    // 2026-09-01 은 화요일. 일요일 시작이면 앞에 8/30, 8/31 이 붙는다.
    expect(monthWindow(2026, 9)).toEqual({ startsOn: '2026-08-30', endsOn: '2026-10-03' })
  })

  it('1일이 주 시작이면 앞을 안 붙인다', () => {
    // 2026-02-01 은 일요일이다.
    expect(monthWindow(2026, 2).startsOn).toBe('2026-02-01')
  })

  it('12월에서 해를 넘긴다 — 여기서 한 칸 틀리면 12월이 하루 짧아진다', () => {
    expect(monthWindow(2026, 12).endsOn).toBe('2027-01-02')
  })

  it('윤년 2월의 마지막 날을 안 빠뜨린다', () => {
    const weeks = weeksOf(monthWindow(2028, 2)).flat()
    expect(weeks).toContain('2028-02-29')
  })

  it('주 시작을 월요일로 바꿀 수 있다', () => {
    expect(monthWindow(2026, 9, 1).startsOn).toBe('2026-08-31')
  })
})

describe('weeksOf', () => {
  it('7일짜리 줄로 자른다', () => {
    const weeks = weeksOf(monthWindow(2026, 9))
    expect(weeks.every((w) => w.length === 7)).toBe(true)
    expect(at(at(weeks, 0), 0)).toBe('2026-08-30')
  })
})

describe('segmentIn', () => {
  const week = at(weeksOf(monthWindow(2026, 9)), 1) // 2026-09-06 ~ 09-12

  it('그 주에 온전히 든 띠', () => {
    expect(segmentIn(entry('A', '2026-09-07', '2026-09-09'), week)).toEqual({
      offset: 1,
      span: 3,
      continuesLeft: false,
      continuesRight: false,
    })
  })

  it('**주를 넘는 띠는 잘린다** — 한 덩어리로 그리면 격자를 벗어난다', () => {
    expect(segmentIn(entry('B', '2026-09-03', '2026-09-15'), week)).toEqual({
      offset: 0,
      span: 7,
      continuesLeft: true,
      continuesRight: true,
    })
  })

  it('안 걸리면 null — 다음 주에서 사라지지 않게 줄마다 다시 묻는다', () => {
    expect(segmentIn(entry('C', '2026-09-20', '2026-09-21'), week)).toBeNull()
  })

  it('하루짜리는 한 칸', () => {
    const found = segmentIn(entry('D', '2026-09-12', '2026-09-12'), week)
    expect(found).toEqual({ offset: 6, span: 1, continuesLeft: false, continuesRight: false })
  })
})

describe('stackIn', () => {
  const week = at(weeksOf(monthWindow(2026, 9)), 1) // 2026-09-06 ~ 09-12

  it('겹치는 띠를 다른 층에 놓는다 — 포개면 하나만 보인다', () => {
    const rows = stackIn(
      [entry('A', '2026-09-07', '2026-09-09'), entry('B', '2026-09-08', '2026-09-10')],
      week,
    )
    expect(rows).toHaveLength(2)
  })

  it('안 겹치면 같은 층에 나란히 둔다 — 줄이 괜히 늘지 않게', () => {
    const rows = stackIn(
      [entry('A', '2026-09-07', '2026-09-08'), entry('B', '2026-09-10', '2026-09-11')],
      week,
    )
    expect(rows).toHaveLength(1)
    expect(at(rows, 0)).toHaveLength(2)
  })

  it('맞닿은 띠는 겹치지 않는다 — 7일 끝, 8일 시작', () => {
    const rows = stackIn(
      [entry('A', '2026-09-07', '2026-09-07'), entry('B', '2026-09-08', '2026-09-08')],
      week,
    )
    expect(rows).toHaveLength(1)
  })

  it('그 주에 안 걸리는 것은 아예 안 놓는다', () => {
    expect(stackIn([entry('A', '2026-10-01', '2026-10-02')], week)).toEqual([])
  })
})
