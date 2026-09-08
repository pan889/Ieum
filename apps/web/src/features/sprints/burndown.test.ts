import { describe, expect, it } from 'vitest'

import type { BurndownPoint } from '@ieum/api-client'

import { plot, toPolyline, type Plot } from './burndown'

function point(
  on_date: string,
  remaining_issues: number,
  total_issues = remaining_issues,
): BurndownPoint {
  return {
    on_date,
    remaining_issues,
    remaining_minutes: remaining_issues * 60,
    total_issues,
    total_minutes: total_issues * 60,
  }
}

/** 없으면 터진다. 시험에서 `undefined` 를 조용히 통과시키지 않으려는 것이다. */
function at<T>(items: readonly T[], index: number): T {
  const found = items[index]
  if (found === undefined) throw new Error(`${index} 번째 값이 없다`)
  return found
}

function drawn(points: BurndownPoint[], endsOn?: string): Plot {
  const found = plot(points, { width: 100, height: 50, endsOn })
  if (found === null) throw new Error('그릴 것이 없다')
  return found
}

describe('plot', () => {
  it('점이 없으면 그릴 것도 없다', () => {
    expect(plot([], { width: 100, height: 50 })).toBeNull()
  })

  it('첫 점은 왼쪽, 마지막 점은 오른쪽 끝에 놓인다', () => {
    const found = drawn([point('2026-09-01', 4), point('2026-09-05', 0)])
    expect(at(found.remaining, 0)).toMatchObject({ x: 0, y: 0 })
    expect(at(found.remaining, 1)).toMatchObject({ x: 100, y: 50 })
  })

  it('**날짜 간격에 비례한다.** 번호순이면 쉰 날의 구멍이 사라진다', () => {
    // 1일, 2일, 6일. 번호순이면 가운데가 50 에 오지만, 날짜로는 20 이다.
    const found = drawn([
      point('2026-09-01', 5),
      point('2026-09-02', 4),
      point('2026-09-06', 0),
    ])
    expect(at(found.remaining, 1).x).toBe(20)
  })

  it('좌표에 그날 값이 붙어 온다 — 화면이 번호로 다시 뒤지지 않게', () => {
    const found = drawn([point('2026-09-01', 3, 8)])
    expect(at(found.remaining, 0)).toMatchObject({
      date: '2026-09-01',
      remaining: 3,
      total: 8,
    })
  })

  it('전체가 늘어난 것이 그대로 보인다 — 매끈하게 만들지 않는다', () => {
    const found = drawn([point('2026-09-01', 3, 3), point('2026-09-02', 5, 8)])
    expect(found.maxY).toBe(8)
    // 둘째 날 전체선이 첫날보다 **위로** 간다 (y 가 작아진다).
    expect(at(found.total, 1).y).toBeLessThan(at(found.total, 0).y)
  })

  it('끝나는 날을 모르면 계획선을 안 그린다', () => {
    expect(drawn([point('2026-09-01', 4), point('2026-09-02', 3)]).ideal).toBeNull()
  })

  it('끝나는 날을 알면 그날 0 으로 내려가는 계획선을 그린다', () => {
    const found = drawn([point('2026-09-01', 4), point('2026-09-02', 3)], '2026-09-11')
    expect(found.ideal).toEqual([
      { x: 0, y: 0 },
      { x: 100, y: 50 },
    ])
  })

  it('늦게 끝난 스프린트에서는 축이 기록을 따른다', () => {
    // 계획은 3일까지였는데 5일까지 돌았다. 축이 3일에서 끊기면 마지막 점이
    // 상자 밖으로 나간다.
    const found = drawn([point('2026-09-01', 4), point('2026-09-05', 1)], '2026-09-03')
    expect(at(found.remaining, 1).x).toBe(100)
    expect(at(found.ideal ?? [], 1).x).toBe(50)
  })

  it('하루짜리 스프린트도 0 으로 나누지 않는다', () => {
    const found = drawn([point('2026-09-01', 2)])
    expect(at(found.remaining, 0)).toMatchObject({ x: 0, y: 0 })
    expect(found.firstDate).toBe('2026-09-01')
    expect(found.lastDate).toBe('2026-09-01')
  })
})

describe('toPolyline', () => {
  it('좌표를 SVG 문자열로', () => {
    expect(
      toPolyline([
        { x: 0, y: 1.234 },
        { x: 10, y: 20 },
      ]),
    ).toBe('0,1.23 10,20')
  })
})
