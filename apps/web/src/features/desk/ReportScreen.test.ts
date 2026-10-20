/**
 * 데스크 리포트 화면의 순수 계산 (feature-map C14).
 *
 * 서버가 옳게 세도 **화면이 창을 잘못 잘라 물어보면** 답은 틀린다. 그리고
 * 그 틀림은 화면에 적힌 날짜와 어긋나므로 아무도 못 본다 — 여기서 잡는다.
 */

import { describe, expect, it } from 'vitest'

import { defaultWindow, humanize, windowBounds } from './ReportScreen'

describe('windowBounds', () => {
  it('끝나는 날의 하루를 통째로 포함한다', () => {
    // 자정으로 보내면 9월 30일에 들어온 티켓이 전부 빠진다. 화면에는
    // "9월 30일까지" 라고 적혀 있으므로 그건 조용한 오답이다.
    expect(windowBounds('2026-09-01', '2026-09-30')).toEqual({
      starts_at: '2026-09-01T00:00:00Z',
      ends_at: '2026-09-30T23:59:59Z',
    })
  })

  it('하루만 고른 것도 하루짜리 창이다', () => {
    const bounds = windowBounds('2026-09-08', '2026-09-08')
    expect(bounds.starts_at < bounds.ends_at).toBe(true)
  })
})

describe('defaultWindow', () => {
  /**
   * 시험은 뉴욕 시간대로 돈다(`vite.config.ts`). 전에는 이 창을 UTC 날짜로
   * 채웠고, 시험도 "UTC 날짜만 남긴다" 를 확인하고 있었다 — 그래서 저녁에
   * 열면 "오늘" 칸에 내일이 적히는 것이 시험으로는 맞는 동작이었다.
   */
  it('늦은 밤에도 로컬 달력의 오늘로 끝난다', () => {
    // 뉴욕 9월 8일 22:30 은 UTC 로는 이미 9월 9일이다.
    const lateNight = new Date(2026, 8, 8, 22, 30)
    expect(lateNight.toISOString().slice(0, 10)).toBe('2026-09-09')

    expect(defaultWindow(lateNight)).toEqual({ from: '2026-08-09', to: '2026-09-08' })
  })

  it('이른 아침에도 로컬 달력의 오늘로 끝난다', () => {
    expect(defaultWindow(new Date(2026, 8, 8, 0, 30)).to).toBe('2026-09-08')
  })

  it('일광절약시간이 끝나는 날을 지나도 30일 전이다', () => {
    // 뉴욕은 11월 1일에 한 시간을 돌린다. 30 × 24시간을 빼면 자정 근처에서
    // 하루가 덜 가거나 더 간다.
    expect(defaultWindow(new Date(2026, 10, 20, 0, 30)).from).toBe('2026-10-21')
    expect(defaultWindow(new Date(2026, 10, 20, 23, 30)).from).toBe('2026-10-21')
  })
})

describe('humanize', () => {
  const t = (key: string, vars: Record<string, number>): string =>
    `${key}:${JSON.stringify(vars)}`

  it('한 시간 미만은 분으로 말한다', () => {
    expect(humanize(1800, t)).toBe('desk:report.minutes:{"minutes":30}')
  })

  it('한 시간부터는 시간으로 말한다', () => {
    expect(humanize(7200, t)).toBe('desk:report.hours:{"hours":2}')
  })

  it('시간은 소수 한 자리까지 남긴다', () => {
    // 2시간 30분을 "2시간" 으로 뭉개면 평균이 실제보다 짧게 읽힌다.
    expect(humanize(9000, t)).toBe('desk:report.hours:{"hours":2.5}')
  })

  it('0초는 0분이다 — `—` 가 아니다', () => {
    // `—` 는 "끝난 것이 없다" 는 뜻이다. 0초를 그렇게 쓰면 하나도 못 끝낸
    // 사람과 즉시 끝낸 사람이 같은 칸에 들어간다.
    expect(humanize(0, t)).toBe('desk:report.minutes:{"minutes":0}')
  })
})
