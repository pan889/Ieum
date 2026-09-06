/**
 * 사람이 쓰는 기간 표기 ↔ 분.
 *
 * 서버 API 는 분 단위 정수만 받는다. 두 형식을 다 받으면 둘이 어긋날 때
 * 어느 쪽이 이기는지 규칙이 하나 더 생기기 때문이다. 사람용 표기는 여기서
 * 분으로 바꿔 보낸다.
 */

/** 하루 근무 시간. 설치마다 다르지만 M1 은 8시간으로 고정한다. */
export const MINUTES_PER_HOUR = 60
export const HOURS_PER_DAY = 8
export const MINUTES_PER_DAY = MINUTES_PER_HOUR * HOURS_PER_DAY
export const DAYS_PER_WEEK = 5
export const MINUTES_PER_WEEK = MINUTES_PER_DAY * DAYS_PER_WEEK

const UNIT_MINUTES: Record<string, number> = {
  w: MINUTES_PER_WEEK,
  d: MINUTES_PER_DAY,
  h: MINUTES_PER_HOUR,
  m: 1,
}

const TOKEN = /(\d+(?:\.\d+)?)\s*([wdhm])/gi

/**
 * "1d 2h 30m" → 630. 해석할 수 없으면 null 이다.
 *
 * 단위 없는 숫자는 **분**으로 본다 — 시간으로 보면 "30" 이 30시간이 되어
 * 조용히 60배 틀린 값이 들어간다.
 */
export function parseDuration(input: string): number | null {
  const text = input.trim().toLowerCase()
  if (text === '') return null

  if (/^\d+(\.\d+)?$/.test(text)) {
    const minutes = Math.round(Number(text))
    return minutes > 0 ? minutes : null
  }

  let total = 0
  let matched = 0
  let consumed = 0
  TOKEN.lastIndex = 0
  for (let m = TOKEN.exec(text); m !== null; m = TOKEN.exec(text)) {
    total += Number(m[1]) * (UNIT_MINUTES[m[2] as string] as number)
    matched += 1
    consumed += m[0].length
  }
  // 인식 못 한 글자가 남으면 (공백 제외) 통째로 거절한다. 부분 해석은
  // 사용자가 의도한 값과 다른 값을 조용히 저장한다.
  if (matched === 0 || consumed !== text.replace(/\s+/g, '').length) return null

  const minutes = Math.round(total)
  return minutes > 0 ? minutes : null
}

/** 630 → "1d 2h 30m". 0 이면 "0m". */
export function formatDuration(minutes: number): string {
  if (minutes <= 0) return '0m'
  const parts: string[] = []
  let rest = Math.round(minutes)
  for (const [unit, size] of [
    ['w', MINUTES_PER_WEEK],
    ['d', MINUTES_PER_DAY],
    ['h', MINUTES_PER_HOUR],
    ['m', 1],
  ] as const) {
    const count = Math.floor(rest / size)
    if (count > 0) {
      parts.push(`${String(count)}${unit}`)
      rest -= count * size
    }
  }
  return parts.join(' ')
}
