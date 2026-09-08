import { describe, expect, it } from 'vitest'

import type { GanttRow } from '@ieum/api-client'

import { arrowsOf, barOf, conflictedIds, daysIn } from './bars'

const WINDOW = { starts_on: '2026-09-01', ends_on: '2026-09-30' }

function row(id: string, starts_on: string, ends_on: string, depends_on: string[] = []): GanttRow {
  return {
    id,
    key: id.toUpperCase(),
    summary: id,
    starts_on,
    ends_on,
    state_name: 'Open',
    state_category: 'todo',
    assignee_id: null,
    priority: 3,
    overdue: false,
    depends_on,
  }
}

describe('daysIn', () => {
  it('양 끝을 포함해 센다', () => {
    expect(daysIn(WINDOW)).toBe(30)
  })
})

describe('barOf', () => {
  it('창 안의 막대', () => {
    expect(barOf({ starts_on: '2026-09-03', ends_on: '2026-09-05' }, WINDOW)).toEqual({
      offset: 2,
      span: 3,
      clippedLeft: false,
      clippedRight: false,
    })
  })

  it('**왼쪽으로 삐져나오면 자르고 표시한다** — 안 하면 이 창에서 시작한 것처럼 보인다', () => {
    expect(barOf({ starts_on: '2026-08-20', ends_on: '2026-09-02' }, WINDOW)).toEqual({
      offset: 0,
      span: 2,
      clippedLeft: true,
      clippedRight: false,
    })
  })

  it('오른쪽으로 삐져나오면 자르고 표시한다', () => {
    expect(barOf({ starts_on: '2026-09-29', ends_on: '2026-10-10' }, WINDOW)).toEqual({
      offset: 28,
      span: 2,
      clippedLeft: false,
      clippedRight: true,
    })
  })

  it('창을 통째로 덮는 막대는 양쪽이 잘린다', () => {
    expect(barOf({ starts_on: '2026-08-01', ends_on: '2026-10-31' }, WINDOW)).toEqual({
      offset: 0,
      span: 30,
      clippedLeft: true,
      clippedRight: true,
    })
  })

  it('안 걸치면 null — NaN 좌표를 만들지 않는다', () => {
    expect(barOf({ starts_on: '2026-07-01', ends_on: '2026-07-02' }, WINDOW)).toBeNull()
  })
})

describe('conflictedIds', () => {
  it('**양쪽을 다 담는다** — 후행만 칠하면 "이 일이 늦었다" 로 읽힌다', () => {
    const found = conflictedIds([{ predecessor: 'a', successor: 'b', overlap_days: 2 }])
    expect([...found].sort()).toEqual(['a', 'b'])
  })

  it('충돌이 없으면 빈 집합', () => {
    expect(conflictedIds([]).size).toBe(0)
  })
})

describe('arrowsOf', () => {
  it('의존을 행 번호 쌍으로 바꾼다', () => {
    const rows = [row('a', '2026-09-01', '2026-09-02'), row('b', '2026-09-03', '2026-09-04', ['a'])]
    expect(arrowsOf(rows)).toEqual([{ from: 0, to: 1 }])
  })

  it('없는 행을 가리키는 의존은 버린다 — 좌표를 못 만드는 화살표는 NaN 이 된다', () => {
    const rows = [row('b', '2026-09-03', '2026-09-04', ['does-not-exist'])]
    expect(arrowsOf(rows)).toEqual([])
  })

  it('한 이슈가 여럿에 의존할 수 있다', () => {
    const rows = [
      row('a', '2026-09-01', '2026-09-02'),
      row('b', '2026-09-02', '2026-09-03'),
      row('c', '2026-09-04', '2026-09-05', ['a', 'b']),
    ]
    expect(arrowsOf(rows)).toEqual([
      { from: 0, to: 2 },
      { from: 1, to: 2 },
    ])
  })
})
