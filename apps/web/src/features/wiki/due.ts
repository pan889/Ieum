/**
 * 기한을 사람이 읽는 급함으로 (B12).
 *
 * 순수 함수로 빼는 이유: 날짜 비교는 조용히 틀린다. 오늘이 기한인 것을
 * "지났다" 로 보이면 사람은 이미 늦은 줄 알고, "남았다" 로 보이면 오늘
 * 해야 하는 것을 내일로 미룬다. 둘 다 화면만 봐서는 못 알아챈다.
 *
 * **`Date` 끼리 비교하지 않는다.** `new Date('2026-09-30')` 은 UTC 자정이고
 * 로컬 시간대가 UTC 뒤면 어제가 된다 — 한국(UTC+9)에서는 안 그렇지만 미국
 * 에서는 그렇다. 그래서 `YYYY-MM-DD` 문자열끼리 비교한다: 그 형식은
 * 사전순 = 시간순이고 시간대가 끼어들 틈이 없다.
 */

export type Urgency = 'overdue' | 'today' | 'soon' | 'later' | 'none'

/** 며칠 안쪽을 "곧" 으로 볼 것인가. */
const SOON_DAYS = 3

/** 그 날짜의 `YYYY-MM-DD`. **로컬 달력 기준이다** — 사람이 보는 오늘이다. */
export function isoDay(at: Date): string {
  const year = at.getFullYear()
  const month = String(at.getMonth() + 1).padStart(2, '0')
  const day = String(at.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function urgencyOf(due: string | null, today: string): Urgency {
  if (due === null || due === '') return 'none'
  if (due < today) return 'overdue'
  if (due === today) return 'today'
  return daysBetween(today, due) <= SOON_DAYS ? 'soon' : 'later'
}

/**
 * 두 `YYYY-MM-DD` 사이의 날 수.
 *
 * 여기서만 `Date` 를 쓴다. **둘 다 UTC 자정으로 만들어** 빼므로 시간대가
 * 끼어들지 않고, 일광절약시간에도 24시간의 배수가 유지된다(로컬 자정으로
 * 만들면 봄·가을에 하루가 23시간·25시간이 되어 나머지가 생긴다).
 */
export function daysBetween(from: string, to: string): number {
  const start = Date.parse(`${from}T00:00:00Z`)
  const end = Date.parse(`${to}T00:00:00Z`)
  if (Number.isNaN(start) || Number.isNaN(end)) return 0
  return Math.round((end - start) / 86_400_000)
}
