/**
 * 날짜만 있는 값(`YYYY-MM-DD`)을 다루는 도우미.
 *
 * 기한·작업일·스프린트 날짜처럼 **시각이 없는 날짜**는 시간대가 없다. 그런데
 * `Date` 는 언제나 한 순간이라, 둘을 섞는 자리마다 하루가 조용히 밀린다:
 *
 * - **오늘을 `toISOString().slice(0, 10)` 으로 만들면 UTC 의 오늘이다.** 서울
 *   에서는 아침 9시 전까지 어제이고, 뉴욕에서는 저녁 8시부터 내일이다.
 * - **`new Date('2026-10-01')` 은 UTC 자정이다.** 그것을 로컬 시간대로 포맷하면
 *   UTC 뒤의 시간대(아메리카 전부)에서 9월 30일로 읽힌다.
 *
 * 그래서 규칙은 둘이다. 사람이 보는 "오늘" 은 **로컬 달력**으로 만들고
 * (`isoDay`), 날짜만 있는 값은 **UTC 로 읽어 UTC 로 포맷한다**(`dayFormatter`)
 * — 읽을 때와 쓸 때 같은 시간대면 그 사이에 하루가 끼어들 틈이 없다.
 */

/** 그 시각의 `YYYY-MM-DD`. **로컬 달력 기준이다** — 사람이 보는 오늘이다. */
export function isoDay(at: Date): string {
  const year = at.getFullYear()
  const month = String(at.getMonth() + 1).padStart(2, '0')
  const day = String(at.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * 로컬 달력으로 `days` 일 뒤(음수면 앞)의 `YYYY-MM-DD`.
 *
 * **밀리초로 빼지 않는다.** `24 * 60 * 60 * 1000` 을 빼면 일광절약시간이
 * 바뀌는 날 하루가 23·25시간이라, 자정 근처에서 하루가 더 가거나 덜 간다.
 * 달력의 "날" 을 더하는 것은 `Date` 의 날짜 칸이 한다.
 */
export function isoDayFrom(at: Date, days: number): string {
  return isoDay(new Date(at.getFullYear(), at.getMonth(), at.getDate() + days))
}

/**
 * `YYYY-MM-DD` 를 **그 날짜 그대로** 글자로 바꾸는 함수를 만든다.
 *
 * 포맷터 하나를 만들어 여러 번 쓴다(목록·차트의 눈금이 한 화면에 수십 개다).
 * 읽지 못하는 값이면 빈 문자열이다 — 화면에 `Invalid Date` 를 띄우지 않는다.
 */
export function dayFormatter(
  locale: string | undefined,
  options: Omit<Intl.DateTimeFormatOptions, 'timeZone'>,
): (iso: string) => string {
  const format = new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' })
  return (iso) => {
    const at = Date.parse(`${iso}T00:00:00Z`)
    return Number.isNaN(at) ? '' : format.format(at)
  }
}
