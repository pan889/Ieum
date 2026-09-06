/**
 * 언어 전환.
 *
 * ko 카탈로그가 다 채워져 있어도 화면이 그 값을 안 쓰면 아무 소용이 없다.
 * 실제로 한동안 그랬다 — 언어를 고르는 즉시 효과 하나가 서버 값으로
 * 되돌려 놓아서, 두 벌의 번역이 통째로 죽어 있었다. 카탈로그를 검사하는
 * 유닛 테스트로는 절대 안 잡힌다.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import type { Page } from '@playwright/test'

import { ADMIN_EMAIL, ADMIN_PASSWORD, expect, signIn, test } from './fixtures'

const CATALOG = fileURLToPath(new URL('../../../packages/i18n/', import.meta.url))
//: 언어 되돌리기는 화면을 거치지 않고 API 로 한다 — 테스트가 실패한 뒤에도
//: 확실히 돌아가야 뒤따르는 테스트가 영어 화면을 본다. (Makefile 의 `make e2e`
//: 와 같은 기본값)
const API = process.env['E2E_API_BASE_URL'] ?? 'http://127.0.0.1:8000'

function catalog(locale: string, namespace: string): Record<string, string> {
  return JSON.parse(readFileSync(`${CATALOG}${locale}/${namespace}.json`, 'utf8')) as Record<
    string,
    string
  >
}

/** 한국어로 봤을 때 나오면 안 되는 영어 문구. 정적이고 ko 가 다른 것만. */
function englishPhrases(): Map<string, string> {
  const out = new Map<string, string>()
  for (const ns of ['common', 'issues', 'wiki', 'search']) {
    const en = catalog('en', ns)
    const ko = catalog('ko', ns)
    for (const [key, value] of Object.entries(en)) {
      // ICU·마크업이 든 문구는 화면 글자와 그대로 비교할 수 없다.
      if (value === ko[key] || /[{<]/.test(value) || value.length < 4) continue
      out.set(value, `${ns}:${key}`)
    }
  }
  return out
}

/**
 * 화면의 '틀' 글자만 모은다.
 *
 * 본문을 통째로 훑으면 워크플로우 상태 이름("Open")이나 사용자가 지은
 * 제목까지 걸린다. 그건 데이터지 번역 대상이 아니다.
 */
async function chromeText(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found = new Set<string>()
    const selector = 'button, label, th, legend, h1, h2, h3, summary, [role="tab"], a'
    for (const element of document.querySelectorAll(selector)) {
      const text = element.textContent.trim()
      if (text && text.length < 80) found.add(text)
    }
    for (const element of document.querySelectorAll('[aria-label], [placeholder]')) {
      for (const attribute of ['aria-label', 'placeholder']) {
        const value = element.getAttribute(attribute)
        if (value) found.add(value.trim())
      }
    }
    return [...found]
  })
}

const ENGLISH = englishPhrases()

async function leftoverEnglish(page: Page): Promise<string[]> {
  return (await chromeText(page)).filter((text) => ENGLISH.has(text))
}

// 시드 DB 를 공유한다. 여기서 바꾼 언어를 되돌리지 않으면 뒤따르는 테스트가
// 전부 한국어 화면을 영어 선택자로 찾는다.
test.afterEach(async ({ request }) => {
  const login = await request.post(`${API}/api/v1/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  })
  const { access_token: token } = (await login.json()) as { access_token: string }
  await request.patch(`${API}/api/v1/users/me`, {
    headers: { authorization: `Bearer ${token}` },
    data: { locale: 'en' },
  })
})

test('언어를 바꾸면 화면이 따라오고 새로고침해도 남는다', async ({ page, consoleErrors }) => {
  await signIn(page)

  // 어느 언어로 시작하든 상관없다 — 바꾸는 것이 주제다.
  await page.getByLabel(/language|언어/i).selectOption('ko')
  await expect(page.getByRole('link', { name: '위키' })).toBeVisible()
  expect(await page.getAttribute('html', 'lang')).toBe('ko')

  await page.reload()
  // 되돌아가지 않는다. 서버가 기억하고 있어야 한다.
  await expect(page.getByRole('link', { name: '위키' })).toBeVisible()

  // 돌아오는 길도 같아야 한다.
  await page.getByLabel(/언어/).selectOption('en')
  await expect(page.getByRole('link', { name: 'Wiki' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('다른 기기에서 열어도 같은 언어다', async ({ page, browser, consoleErrors }) => {
  await signIn(page)
  await page.getByLabel(/language|언어/i).selectOption('ko')
  await expect(page.getByRole('link', { name: '위키' })).toBeVisible()

  // 저장한 것이 브라우저가 아니라 서버라면, 아무것도 모르는 브라우저에서
  // 로그인해도 한국어여야 한다.
  const fresh = await browser.newContext()
  const other = await fresh.newPage()
  await signIn(other)
  await expect(other.getByRole('link', { name: '위키' })).toBeVisible()
  await fresh.close()

  expect(consoleErrors).toEqual([])
})

test('위키·검색 화면에 영어 UI 문구가 남지 않는다', async ({ page, consoleErrors }) => {
  const key = 'K' + Math.random().toString(36).slice(2, 6).toUpperCase()
  await signIn(page)
  await page.getByLabel(/language|언어/i).selectOption('ko')
  await expect(page.getByRole('link', { name: '위키' })).toBeVisible()

  await page.goto('/wiki')
  await page.getByRole('button', { name: /새 스페이스/ }).click()
  await page.getByLabel(/^키$/).fill(key)
  await page.getByLabel(/^이름$/).fill(`문서 ${key}`)
  await page.getByRole('button', { name: /스페이스 만들기/ }).click()
  await page.getByLabel(/스페이스 찾기/).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()

  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ 새 문서$/ }).click()
  await page.getByLabel(/^제목$/).fill('점검 문서')
  await page.getByRole('button', { name: /^만들기$/ }).click()
  await expect(page.getByRole('heading', { name: '점검 문서' })).toBeVisible()

  for (const url of ['/wiki', `/wiki/${key}`, `/wiki/${key}/점검-문서`, '/search?q=점검', '/issues']) {
    await page.goto(url)
    await expect(page.locator('main, body').first()).toBeVisible()
    expect(await leftoverEnglish(page), `남은 영어: ${url}`).toEqual([])
  }

  // 편집·이력 패널도 연다. 안 열면 그 안의 문구는 한 번도 안 보인다.
  await page.goto(`/wiki/${key}/점검-문서`)
  for (const name of [/^편집$/, /^이력$/]) {
    await page.getByRole('button', { name }).first().click()
    await expect(page.locator('main, body').first()).toBeVisible()
    expect(await leftoverEnglish(page), `남은 영어: ${String(name)}`).toEqual([])
  }

  expect(consoleErrors).toEqual([])
})
