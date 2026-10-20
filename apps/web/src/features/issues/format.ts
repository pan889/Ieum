import { dayFormatter } from '@/shared/day'
import { i18next } from '@/shared/i18n'

/** 상태 분류 뱃지 톤. 서버 문자열을 그대로 받는다. */
export function categoryTone(category: string): 'todo' | 'in_progress' | 'done' | 'neutral' {
  return category === 'todo' || category === 'in_progress' || category === 'done'
    ? category
    : 'neutral'
}

export function priorityLabel(priority: number): string {
  // 1~5 밖의 값이 오면 숫자를 그대로 보여준다. 서버 스키마가 바뀌어도
  // 화면이 빈칸이 되지는 않는다.
  const key = `issues:priority.${String(priority)}`
  const label = i18next.t(key, { defaultValue: '' })
  return label || String(priority)
}

/** 시각 없이 날짜만 있는 값. 기한(`due_date`)·작업일(`work_date`)이 이것이다. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/**
 * 날짜를 사람이 읽는 글자로. 시각(`created_at`)과 날짜(`due_date`)를 함께 받는다.
 *
 * **날짜만 있는 값은 UTC 로 쓴다.** `new Date('2026-10-01')` 은 UTC 자정이라,
 * 로컬로 포맷하면 UTC 뒤의 시간대(아메리카 전부)에서 기한이 하루 앞당겨
 * 적힌다 — 10월 1일까지인 일이 9월 30일까지로 보인다. 시각이 있는 값은 그
 * 순간이 보는 사람에게 언제인지가 맞으므로 로컬로 쓴다.
 */
export function formatDate(value: string | null): string {
  if (!value) return ''
  if (DATE_ONLY.test(value)) return dayFormatter(i18next.language, { dateStyle: 'medium' })(value)
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(i18next.language, { dateStyle: 'medium' }).format(date)
}

export function formatDateTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(i18next.language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

/** 상대 시간. "3일 전" 처럼 목록에서 훑기 좋다. */
export function formatRelative(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['day', 86_400],
    ['hour', 3600],
    ['minute', 60],
  ]
  const formatter = new Intl.RelativeTimeFormat(i18next.language, { numeric: 'auto' })
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit)
    }
  }
  return formatter.format(Math.round(seconds), 'second')
}
