import i18next from 'i18next'
import ICU from 'i18next-icu'
import { initReactI18next } from 'react-i18next'

import adminEn from '@ieum/i18n/en/admin.json'
import authEn from '@ieum/i18n/en/auth.json'
import boardsEn from '@ieum/i18n/en/boards.json'
import commonEn from '@ieum/i18n/en/common.json'
import deskEn from '@ieum/i18n/en/desk.json'
import errorsEn from '@ieum/i18n/en/errors.json'
import issuesEn from '@ieum/i18n/en/issues.json'
import markdownEn from '@ieum/i18n/en/markdown.json'
import notificationsEn from '@ieum/i18n/en/notifications.json'
import projectsEn from '@ieum/i18n/en/projects.json'
import searchEn from '@ieum/i18n/en/search.json'
import wikiEn from '@ieum/i18n/en/wiki.json'
import adminKo from '@ieum/i18n/ko/admin.json'
import authKo from '@ieum/i18n/ko/auth.json'
import boardsKo from '@ieum/i18n/ko/boards.json'
import commonKo from '@ieum/i18n/ko/common.json'
import deskKo from '@ieum/i18n/ko/desk.json'
import errorsKo from '@ieum/i18n/ko/errors.json'
import issuesKo from '@ieum/i18n/ko/issues.json'
import markdownKo from '@ieum/i18n/ko/markdown.json'
import notificationsKo from '@ieum/i18n/ko/notifications.json'
import projectsKo from '@ieum/i18n/ko/projects.json'
import searchKo from '@ieum/i18n/ko/search.json'
import wikiKo from '@ieum/i18n/ko/wiki.json'

export const SUPPORTED_LOCALES = ['en', 'ko'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

const resources = {
  en: {
    common: commonEn,
    auth: authEn,
    admin: adminEn,
    desk: deskEn,
    errors: errorsEn,
    projects: projectsEn,
    issues: issuesEn,
    boards: boardsEn,
    notifications: notificationsEn,
    wiki: wikiEn,
    markdown: markdownEn,
    search: searchEn,
  },
  ko: {
    common: commonKo,
    auth: authKo,
    admin: adminKo,
    desk: deskKo,
    errors: errorsKo,
    projects: projectsKo,
    issues: issuesKo,
    boards: boardsKo,
    notifications: notificationsKo,
    wiki: wikiKo,
    markdown: markdownKo,
    search: searchKo,
  },
} as const

/**
 * 로드할 네임스페이스. `resources` 에서 뽑는다 — 두 곳에 적으면 어긋난다.
 *
 * `en` 을 기준으로 삼는 것은 `fallbackLng` 가 `en` 이기 때문이다: en 에 없는
 * 네임스페이스는 어차피 떨어질 곳이 없다.
 */
export const NAMESPACES = Object.keys(resources.en) as (keyof typeof resources.en)[]

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
      // **목록을 손으로 들고 있지 않는다.** 여기에 열 개를 적어 두었고
      // `admin`·`desk` 두 개가 빠져 있었다 — 카탈로그를 더하면서 이 줄을
      // 잊은 것이다. 지금은 `resources` 가 인라인이라 i18next 가 그래도
      // 찾아 주므로 화면은 멀쩡했고, 그래서 아무도 몰랐다. 백엔드로 카탈로그를
      // 나눠 받는 순간(또는 `partialBundledLanguages` 를 켜는 순간) 그 두
      // 네임스페이스만 조용히 비어 있게 된다.
      ns: NAMESPACES,
      interpolation: { escapeValue: false },
      returnNull: false,
    })
  document.documentElement.lang = locale
  return i18next
}

export { i18next }
