import { isApiError } from '@ieum/api-client'
import i18next from 'i18next'

/**
 * 에러를 사용자에게 보여줄 문구로 바꾼다.
 *
 * 서버 message 를 그대로 쓰지 않는다 — 서버 문구는 한국어 고정이고
 * 디버깅용이다. code 로 번역하고, details 를 ICU 변수로 넘긴다
 * (docs/architecture/i18n.md 1절).
 *
 * t 를 인자로 받지 않고 인스턴스를 직접 쓴다. 호출부는 이미 useTranslation 으로
 * 구독하고 있어 언어가 바뀌면 다시 렌더되고, 그때 현재 언어로 다시 계산된다.
 */
export function describeError(error: unknown): string {
  if (!isApiError(error)) {
    return i18next.t('errors:internal.error')
  }
  return i18next.t(error.translationKey, {
    ...error.details,
    defaultValue: i18next.t('errors:internal.error'),
  })
}

/** 화면 안내가 필요한 특정 상황인지 판별한다. */
export function hasCode(error: unknown, code: string): boolean {
  return isApiError(error) && error.code === code
}
