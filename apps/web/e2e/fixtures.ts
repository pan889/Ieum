import { expect, test as base } from '@playwright/test'
import type { Page } from '@playwright/test'

export const ADMIN_EMAIL = process.env['SEED_ADMIN_EMAIL'] ?? 'admin@example.com'
export const ADMIN_PASSWORD = process.env['SEED_ADMIN_PASSWORD'] ?? 'seed-admin-password-1234'

/** 테스트마다 자기 프로젝트를 만든다. 시드 DB 를 공유해도 서로 안 밟는다. */
export function projectKey(): string {
  return 'E' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

export async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/email/i).fill(ADMIN_EMAIL)
  await page.getByLabel(/password/i).fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForURL(/\/projects/)
}

export async function createProject(page: Page, key: string, name = 'E2E'): Promise<void> {
  await page.getByRole('link', { name: /^projects$/i }).click()
  await page.waitForURL(/\/projects$/)
  await page.getByRole('button', { name: /new project/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(name)
  await page.getByRole('button', { name: /create project/i }).click()
  // 목록의 첫 페이지에 있으리라 기대하지 않는다. 프로젝트는 키 순이라
  // 새로 만든 게 몇 페이지 뒤에 있을 수 있다 — 검색으로 확인한다.
  await page.getByLabel(/find a project/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
  await page.getByLabel(/find a project/i).fill('')
}

export async function createIssue(
  page: Page,
  key: string,
  summary: string,
  options: { priority?: '1' | '2' | '3' | '4' | '5' } = {},
): Promise<string> {
  await page.goto('/issues/new')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
  await page.getByLabel(/^summary$/i).fill(summary)
  if (options.priority) await page.getByLabel(/^priority$/i).selectOption(options.priority)
  await page.getByRole('button', { name: /create issue/i }).click()
  await page.waitForURL(new RegExp(`/issues/${key}-`))
  return page.url().split('/').pop() as string
}

/**
 * 앱이 의도적으로 내는 요청 실패.
 *
 * 콘솔 문자열이 아니라 URL+상태로 거른다. "401 (Unauthorized)" 로 걸러 두면
 * 진짜 401 도 같이 묻힌다.
 */
function isExpectedFailure(url: string, status: number): boolean {
  // 부팅 시 로그인 여부를 서버에 묻는다. 익명이면 401 이 정상이다.
  if (status === 401 && url.includes('/api/v1/auth/me')) return true
  // 개발 서버에는 favicon 이 없다.
  if (status === 404 && url.includes('favicon')) return true
  return false
}

/**
 * 예상 못 한 오류를 실패로 만든다.
 *
 * 화면은 멀쩡해 보이는데 콘솔에만 터지는 오류가 제일 오래 살아남는다.
 * 배열에 담아 두고 테스트가 직접 단언한다 — 4xx 를 일부러 내는 테스트는
 * 자기가 예상하는 것만 빼고 보면 된다.
 */
export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = []
    page.on('response', (response) => {
      const status = response.status()
      if (status < 400) return
      if (isExpectedFailure(response.url(), status)) return
      errors.push(`${String(status)} ${response.url()}`)
    })
    page.on('pageerror', (error) => { errors.push(String(error)); })
    await use(errors)
  },
})

export { expect }
