/**
 * 번다운 그림의 좌표 계산 (M5).
 *
 * **되짚어 계산하지 않는다.** 서버가 그날 찍은 점을 그대로 놓는다 — 여기서
 * 하는 일은 좌표 변환뿐이다. 이 파일이 서버 값을 "매끈하게" 만들기 시작하면
 * 범위가 늘어난 사실이 그림에서 사라진다.
 */

import type { BurndownPoint } from '@ieum/api-client'

export interface Point {
  x: number
  y: number
}

/**
 * 좌표에 **그날 값을 붙여 둔 점.**
 *
 * 그림과 글자가 같은 배열에서 나와야 한다. 좌표만 돌려주고 화면이 원래
 * 배열을 번호로 다시 뒤지면, 두 배열의 길이가 어긋나는 날 조용히 다른
 * 날짜의 숫자를 읽는다.
 */
export interface Mark extends Point {
  date: string
  remaining: number
  total: number
}

export interface Plot {
  /** 그날 남아 있던 이슈. 점을 찍는 선이다. */
  remaining: Mark[]
  /** 그날 스프린트에 들어 있던 전체. **위로 꺾이면 범위가 늘어난 것이다.** */
  total: Point[]
  /**
   * 계획선. 끝나는 날을 아는 스프린트에만 그린다.
   *
   * 모르면 안 그린다 — 마지막으로 찍힌 날(보통 오늘)을 끝으로 삼으면 계획선이
   * 매일 움직여서, 언제 봐도 "계획대로" 로 보인다.
   */
  ideal: [Point, Point] | null
  /** y 축 꼭대기. */
  maxY: number
  /** x 축 양 끝에 적을 날짜. */
  firstDate: string
  lastDate: string
}

export interface PlotBox {
  width: number
  height: number
  /** 끝나기로 한 날 (`YYYY-MM-DD`). 없으면 계획선을 그리지 않는다. */
  endsOn?: string | undefined
}

const DAY = 24 * 60 * 60 * 1000

/** `YYYY-MM-DD` → epoch 일. 시각·타임존을 섞지 않으려고 UTC 자정으로 읽는다. */
function dayOf(iso: string): number {
  return Math.floor(Date.parse(`${iso}T00:00:00Z`) / DAY)
}

/**
 * 점들을 상자 안 좌표로.
 *
 * x 는 **날짜 간격에 비례한다** — 번호순으로 놓으면 워커가 하루 쉰 날의
 * 구멍이 사라지고, 이틀치 변화가 하루처럼 보인다.
 */
export function plot(points: BurndownPoint[], box: PlotBox): Plot | null {
  const [head, ...rest] = points
  if (head === undefined) return null
  const tail = rest.at(-1) ?? head

  const first = dayOf(head.on_date)
  const planned = box.endsOn === undefined ? null : dayOf(box.endsOn)
  // 계획한 끝날이 마지막 기록보다 앞서면(늦게 끝난 스프린트) 축은 기록을 따른다.
  const lastRecorded = dayOf(tail.on_date)
  const span = Math.max(1, Math.max(lastRecorded, planned ?? lastRecorded) - first)

  const maxY = Math.max(1, ...points.map((p) => Math.max(p.total_issues, p.remaining_issues)))
  const x = (day: number) => ((day - first) / span) * box.width
  const y = (value: number) => box.height - (value / maxY) * box.height

  return {
    remaining: points.map((p) => ({
      x: x(dayOf(p.on_date)),
      y: y(p.remaining_issues),
      date: p.on_date,
      remaining: p.remaining_issues,
      total: p.total_issues,
    })),
    total: points.map((p) => ({ x: x(dayOf(p.on_date)), y: y(p.total_issues) })),
    ideal:
      planned === null
        ? null
        : [
            { x: x(first), y: y(head.remaining_issues) },
            { x: x(planned), y: y(0) },
          ],
    maxY,
    firstDate: head.on_date,
    lastDate: tail.on_date,
  }
}

/** `<polyline points="...">` 에 넣을 문자열. */
export function toPolyline(points: Point[]): string {
  return points.map((p) => `${round(p.x)},${round(p.y)}`).join(' ')
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}
