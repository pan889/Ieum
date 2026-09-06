import i18next from 'i18next'
import ICU from 'i18next-icu'
import { initReactI18next } from 'react-i18next'

import authEn from '@ieum/i18n/en/auth.json'
import boardsEn from '@ieum/i18n/en/boards.json'
import commonEn from '@ieum/i18n/en/common.json'
import errorsEn from '@ieum/i18n/en/errors.json'
import issuesEn from '@ieum/i18n/en/issues.json'
import notificationsEn from '@ieum/i18n/en/notifications.json'
import projectsEn from '@ieum/i18n/en/projects.json'
import authKo from '@ieum/i18n/ko/auth.json'
import boardsKo from '@ieum/i18n/ko/boards.json'
import commonKo from '@ieum/i18n/ko/common.json'
import errorsKo from '@ieum/i18n/ko/errors.json'
import issuesKo from '@ieum/i18n/ko/issues.json'
import notificationsKo from '@ieum/i18n/ko/notifications.json'
import projectsKo from '@ieum/i18n/ko/projects.json'

export const SUPPORTED_LOCALES = ['en', 'ko'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

const resources = {
  en: {
    common: commonEn,
    auth: authEn,
    errors: errorsEn,
    projects: projectsEn,
    issues: issuesEn,
    boards: boardsEn,
    notifications: notificationsEn,
  },
  ko: {
    common: commonKo,
    auth: authKo,
    errors: errorsKo,
    projects: projectsKo,
    issues: issuesKo,
    boards: boardsKo,
    notifications: notificationsKo,
  },
} as const

const STORAGE_KEY = 'ieum.locale'

/**
 * 언어 결정 순서: 사용자 설정 → 저장된 선택 → Accept-Language → en
 * (docs/architecture/i18n.md 3절). 서버가 사용자 locale 을 주면 그게 이긴다.
 */
export function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && (SUPPORTED_LOCALES as readonly string[]).includes(saved)) {
      return saved as Locale
    }
  } catch {
    /* 프라이빗 모드 */
  }
  for (const tag of navigator.languages) {
    const base = tag.split('-')[0]
    if (base && (SUPPORTED_LOCALES as readonly string[]).includes(base)) {
      return base as Locale
    }
  }
  return 'en'
}

export function persistLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    /* 무시 */
  }
}

export async function setLocale(locale: Locale): Promise<void> {
  persistLocale(locale)
  document.documentElement.lang = locale
  await i18next.changeLanguage(locale)
}

export async function initI18n(locale: Locale = detectLocale()): Promise<typeof i18next> {
  await i18next
    .use(ICU)
    .use(initReactI18next)
    .init({
      resources,
      lng: locale,
      // 새 언어를 추가했는데 키를 못 채웠으면 en 으로 떨어진다 (i18n.md 5절).
      fallbackLng: 'en',
      defaultNS: 'common',
      ns: ['common', 'auth', 'errors', 'projects', 'issues', 'boards', 'notifications'],
      interpolation: { escapeValue: false },
      returnNull: false,
    })
  document.documentElement.lang = locale
  return i18next
}

export { i18next }
