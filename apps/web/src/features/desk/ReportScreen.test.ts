/**
 * 데스크 리포트 화면의 순수 계산 (feature-map C14).
 *
 * 서버가 옳게 세도 **화면이 창을 잘못 잘라 물어보면** 답은 틀린다. 그리고
 * 그 틀림은 화면에 적힌 날짜와 어긋나므로 아무도 못 본다 — 여기서 잡는다.
 */

import { describe, expect, it } from 'vitest'

import { humanize, isoDay, windowBounds } from './ReportScreen'

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

describe('isoDay', () => {
  it('UTC 날짜만 남긴다', () => {
    expect(isoDay(new Date('2026-09-08T15:30:00Z'))).toBe('2026-09-08')
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
