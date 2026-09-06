import { expect, test as base } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

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
  // 폼이 닫히면 생성이 끝난 것이다. 끝나기 전에 검색하면 목록 질의가
  // 생성보다 먼저 끝나 버려서 방금 만든 프로젝트를 못 찾는다.
  await expect(page.getByRole('button', { name: /create project/i })).toHaveCount(0)
  // 목록의 첫 페이지에 있으리라 기대하지 않는다. 프로젝트는 키 순이라
  // 새로 만든 게 몇 페이지 뒤에 있을 수 있다 — 검색으로 확인한다.
  await page.getByLabel(/find a project/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
  await page.getByLabel(/find a project/i).fill('')
}

/** 위키 스페이스를 만든다. 여섯 개 스펙이 같은 것을 여섯 번 적어 두고 있었다. */
export async function createSpace(page: Page, key: string): Promise<void> {
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  // 폼이 닫히면 생성이 끝난 것이다. 끝나기 전에 검색하면 목록 질의가
  // 생성보다 먼저 끝나 버려서 방금 만든 스페이스를 못 찾는다.
  await expect(page.getByRole('button', { name: /create space/i })).toHaveCount(0)
  await page.getByLabel(/find a space/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
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
 * 마크다운 소스 입력칸.
 *
 * 편집기는 서식 모드가 기본이다. 마크다운을 글자 그대로 넣으려면 소스 탭을
 * 먼저 골라야 한다 — 서식 모드에 `fill` 하면 입력 규칙을 타지 않아서
 * `# 제목` 이 제목이 아니라 글자로 들어앉는다.
 */
export async function bodyField(page: Page): Promise<Locator> {
  const source = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await source.getAttribute('aria-selected')) !== 'true') await source.click()
  // 라벨은 화면마다 다르다(Body·Description). 탭 패널 안의 textarea 로 잡으면
  // 어느 화면에서든 같은 헬퍼가 쓰인다.
  return page.locator('[role="tabpanel"] textarea').first()
}

/** 소스 모드로 본문을 채운다. */
export async function writeBody(page: Page, text: string): Promise<void> {
  await (await bodyField(page)).fill(text)
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
