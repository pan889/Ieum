/**
 * 달력 격자 (A18, M5).
 *
 * **날짜 계산은 전부 여기서 한다.** 화면 컴포넌트가 `new Date()` 를 만지기
 * 시작하면 서머타임과 타임존이 조용히 하루를 옮긴다 — 그래서 여기서는
 * `YYYY-MM-DD` 문자열과 **정수(epoch 일)** 로만 센다.
 */

import type { CalendarEntry } from '@ieum/api-client'

const DAY = 24 * 60 * 60 * 1000

/** `YYYY-MM-DD` → epoch 일. UTC 자정으로 읽어 시각을 섞지 않는다. */
export function dayOf(iso: string): number {
  return Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY)
}

/** epoch 일 → `YYYY-MM-DD`. */
export function isoOf(day: number): string {
  return new Date(day * DAY).toISOString().slice(0, 10)
}

/**
 * 주가 시작하는 요일. 0 = 일요일.
 *
 * 한국 달력은 일요일에 시작한다. ISO 는 월요일이지만, 이 값을 로케일에서
 * 끌어오는 표준 API(`Intl.Locale.weekInfo`)는 아직 브라우저마다 다르다 —
 * 없는 것에 기대느니 기본값을 정하고 바꿀 수 있게 둔다.
 */
export const WEEK_STARTS_ON = 0

/** epoch 일의 요일 (0 = 일요일). 1970-01-01 은 목요일(4)이었다. */
export function weekdayOf(day: number): number {
  return (((day + 4) % 7) + 7) % 7
}

export interface MonthWindow {
  /** 격자의 첫 칸 (그 달 1일이 든 주의 시작). */
  startsOn: string
  /** 격자의 마지막 칸 (그 달 말일이 든 주의 끝). */
  endsOn: string
}

/**
 * 그 달을 담는 격자의 창.
 *
 * **주 경계까지 넓힌다.** 1일이 수요일이면 그 앞의 일~화도 격자에 있고,
 * 거기 걸친 일이 보여야 한다 — 안 보이면 "월초에 아무것도 없다" 로 읽힌다.
 */
export function monthWindow(year: number, month: number, weekStartsOn = WEEK_STARTS_ON): MonthWindow {
  const first = dayOf(`${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-01`)
  const lastOfMonth = dayOf(
    month === 12
      ? `${String(year + 1).padStart(4, '0')}-01-01`
      : `${String(year).padStart(4, '0')}-${String(month + 1).padStart(2, '0')}-01`,
  ) - 1

  const leading = (((weekdayOf(first) - weekStartsOn) % 7) + 7) % 7
  const trailing = (((weekStartsOn + 6 - weekdayOf(lastOfMonth)) % 7) + 7) % 7
  return { startsOn: isoOf(first - leading), endsOn: isoOf(lastOfMonth + trailing) }
}

/** 격자를 7일짜리 줄로 자른다. 각 칸은 `YYYY-MM-DD`. */
export function weeksOf(window: MonthWindow): string[][] {
  const first = dayOf(window.startsOn)
  const last = dayOf(window.endsOn)
  const weeks: string[][] = []
  for (let start = first; start <= last; start += 7) {
    const row: string[] = []
    for (let i = 0; i < 7 && start + i <= last; i += 1) row.push(isoOf(start + i))
    weeks.push(row)
  }
  return weeks
}

export interface Segment {
  /** 이 줄에서 몇 번째 칸부터인가 (0-6). */
  offset: number
  /** 몇 칸을 차지하는가 (1-7). */
  span: number
  /** 왼쪽이 잘렸나 — 이 띠가 지난 주에서 이어져 온다. */
  continuesLeft: boolean
  /** 오른쪽이 잘렸나 — 다음 주로 이어진다. */
  continuesRight: boolean
}

/**
 * 이 띠가 그 주에서 차지하는 칸. 안 걸리면 `null`.
 *
 * **주를 넘는 띠는 줄마다 잘라야 한다.** 한 덩어리로 그리려 하면 격자를
 * 벗어나고, 첫 줄에만 그리면 다음 주에서 그 일이 사라진다.
 */
export function segmentIn(entry: CalendarEntry, week: string[]): Segment | null {
  const weekStart = week[0]
  const weekEnd = week[week.length - 1]
  if (weekStart === undefined || weekEnd === undefined) return null

  const from = dayOf(weekStart)
  const to = dayOf(weekEnd)
  const start = dayOf(entry.starts_on)
  const end = dayOf(entry.ends_on)
  if (end < from || start > to) return null

  const clippedStart = Math.max(start, from)
  const clippedEnd = Math.min(end, to)
  return {
    offset: clippedStart - from,
    span: clippedEnd - clippedStart + 1,
    continuesLeft: start < from,
    continuesRight: end > to,
  }
}

/**
 * 한 주에 놓이는 띠들을 겹치지 않게 층으로 쌓는다.
 *
 * 층을 안 나누면 같은 날의 띠 둘이 포개져서 하나만 보인다 — **없는 여유를
 * 있다고 읽는** 가장 흔한 방법이다.
 */
export function stackIn(entries: CalendarEntry[], week: string[]): (CalendarEntry & Segment)[][] {
  const rows: (CalendarEntry & Segment)[][] = []
  // 먼저 시작하는 것, 같으면 긴 것부터. 짧은 것을 먼저 놓으면 긴 띠가
  // 아래로 밀려서 줄이 불필요하게 늘어난다.
  const placed = entries
    .map((entry) => {
      const segment = segmentIn(entry, week)
      return segment === null ? null : { ...entry, ...segment }
    })
    .filter((v): v is CalendarEntry & Segment => v !== null)
    .sort((a, b) => a.offset - b.offset || b.span - a.span || a.key.localeCompare(b.key))

  for (const item of placed) {
    const row = rows.find((existing) =>
      existing.every((other) => item.offset >= other.offset + other.span || other.offset >= item.offset + item.span),
    )
    if (row) row.push(item)
    else rows.push([item])
  }
  return rows
}
