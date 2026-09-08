/**
 * 간트 막대의 자리 계산 (A17, M5).
 *
 * 달력의 `grid.ts` 와 같은 원칙: **날짜는 정수(epoch 일)로만 센다.** 화면
 * 컴포넌트가 `Date` 를 만지면 서머타임이 조용히 하루를 옮긴다.
 */

import type { GanttConflict, GanttRow } from '@ieum/api-client'

import { dayOf, isoOf } from '@/features/calendar/grid'

export interface Bar {
  /** 창의 왼쪽에서 몇 칸 떨어졌나 (0 이상). */
  offset: number
  /** 몇 칸을 차지하나 (1 이상). */
  span: number
  /** 왼쪽이 창 밖으로 잘렸나. */
  clippedLeft: boolean
  clippedRight: boolean
}

/**
 * 창 안에서 이 막대가 차지하는 칸.
 *
 * **창 밖으로 삐져나온 쪽은 자르고 잘렸다고 표시한다.** 안 자르면 막대가
 * 그림을 벗어나고, 표시를 안 하면 "이 창에서 시작해 이 창에서 끝난다" 로
 * 읽힌다 — 지난달부터 이어진 일이 이번 달에 시작한 것처럼 보인다.
 */
export function barOf(row: { starts_on: string; ends_on: string }, window: { starts_on: string; ends_on: string }): Bar | null {
  const from = dayOf(window.starts_on)
  const to = dayOf(window.ends_on)
  const start = dayOf(row.starts_on)
  const end = dayOf(row.ends_on)
  if (end < from || start > to) return null

  const clippedStart = Math.max(start, from)
  const clippedEnd = Math.min(end, to)
  return {
    offset: clippedStart - from,
    span: clippedEnd - clippedStart + 1,
    clippedLeft: start < from,
    clippedRight: end > to,
  }
}

/**
 * 달의 첫 날과, 거기서 `months` 달째의 마지막 날.
 *
 * **달력과 달리 주 경계로 넓히지 않는다.** 간트의 축은 날이지 주가 아니라서,
 * 앞뒤로 며칠 붙이면 축만 길어지고 읽을 것은 늘지 않는다.
 */
export function monthSpan(year: number, month: number, months: number): { starts_on: string; ends_on: string } {
  const pad = (n: number) => String(n).padStart(2, '0')
  const startsOn = `${String(year).padStart(4, '0')}-${pad(month)}-01`
  // 다음 달 1일의 하루 전이 이 달의 마지막 날이다. 달마다 며칠인지 세지
  // 않아도 되고, 윤년도 저절로 맞는다.
  const afterMonth = month + months
  const afterYear = year + Math.floor((afterMonth - 1) / 12)
  const wrapped = ((afterMonth - 1) % 12) + 1
  const after = `${String(afterYear).padStart(4, '0')}-${pad(wrapped)}-01`
  return { starts_on: startsOn, ends_on: isoOf(dayOf(after) - 1) }
}

/** 창의 날 수. 격자 열 수가 된다. */
export function daysIn(window: { starts_on: string; ends_on: string }): number {
  return dayOf(window.ends_on) - dayOf(window.starts_on) + 1
}

/**
 * 충돌에 걸린 이슈 id 전부.
 *
 * 화면은 **양쪽 막대**를 표시해야 한다. 후행만 칠하면 "이 일이 늦었다" 로
 * 읽히는데, 실제로는 두 일의 **순서**가 어긋난 것이다.
 */
export function conflictedIds(conflicts: GanttConflict[]): Set<string> {
  const out = new Set<string>()
  for (const conflict of conflicts) {
    out.add(conflict.predecessor)
    out.add(conflict.successor)
  }
  return out
}

/**
 * 막대 사이의 의존을 **행 번호 쌍**으로. 화살표를 그릴 좌표의 재료다.
 *
 * 창 안에 없는 의존은 서버가 이미 뺐지만(`links_outside` 로 세어 준다),
 * 여기서도 없는 행을 가리키는 쌍은 버린다 — 좌표를 못 만드는 화살표를
 * 넘기면 화면이 NaN 을 그린다.
 */
export function arrowsOf(rows: GanttRow[]): { from: number; to: number }[] {
  const index = new Map(rows.map((row, i) => [row.id, i]))
  const out: { from: number; to: number }[] = []
  for (const [to, row] of rows.entries()) {
    for (const id of row.depends_on) {
      const from = index.get(id)
      if (from !== undefined) out.push({ from, to })
    }
  }
  return out
}
