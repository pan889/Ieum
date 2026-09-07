/**
 * 감사 로그 조회·내보내기와 기기 관리 (auth.md 6절).
 *
 * 브라우저에서만 보이는 것이 둘 있다. 하나는 **CSV 내려받기** — 액세스
 * 토큰이 메모리에만 있어 앵커에 서버 경로를 걸면 401 로 죽는다(위키
 * 내보내기와 같은 자리). 다른 하나는 **기기 하나만 끊기** — 서버가 세션을
 * 들고 있어야 즉시 죽는데, 그게 화면에 실제로 반영되는지는 눌러 봐야 안다.
 */

import { readFile } from 'node:fs/promises'

import { expect, signIn, test } from './fixtures'

test('한 일이 감사 로그에 남고 최신순으로 보인다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/audit')

  const rows = page.locator('tbody tr')
  await expect(rows.first()).toBeVisible()
  // 방금 로그인했다. 맨 위가 그 기록이어야 한다.
  await expect(rows.first()).toContainText('auth.login.succeeded')
  // id 만 있으면 화면에서 사람을 못 알아본다.
  await expect(rows.first()).toContainText('admin@example.com')

  expect(consoleErrors).toEqual([])
})

test('행동으로 거른다 — 점으로 끝나면 그 영역 전체다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/audit')
  await expect(page.locator('tbody tr').first()).toBeVisible()

  await page.getByLabel(/^action$/i).selectOption('auth.login.succeeded')
  const actions = page.locator('tbody tr td:nth-child(2)')
  await expect(actions.first()).toHaveText('auth.login.succeeded')
  const only = await actions.allInnerTexts()
  expect(new Set(only)).toEqual(new Set(['auth.login.succeeded']))

  // 영역 전체. 하나하나 고르지 않아도 된다.
  await page.getByLabel(/^action$/i).selectOption('auth.')
  await expect(page.locator('tbody tr').first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('CSV 로 내려받는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/audit')
  await expect(page.locator('tbody tr').first()).toBeVisible()

  const saved = page.waitForEvent('download')
  await page.getByRole('button', { name: /export csv/i }).click()
  const download = await saved
  expect(download.suggestedFilename()).toMatch(/^ieum-audit-\d{4}-\d{2}-\d{2}\.csv$/)

  const text = await readFile(await download.path(), 'utf-8')
  // BOM 이 없으면 엑셀이 UTF-8 을 로컬 인코딩으로 읽어 한국어가 깨진다.
  expect(text.charCodeAt(0)).toBe(0xfeff)
  expect(text).toContain('created_at,action,actor_email')
  expect(text).toContain('admin@example.com')

  expect(consoleErrors).toEqual([])
})

test('기기 하나만 끊으면 나머지는 그대로다', async ({ page, browser, consoleErrors }) => {
  await signIn(page)

  // 두 번째 기기. 같은 계정으로 다른 창에서 들어온다.
  const other = await browser.newContext()
  const otherPage = await other.newPage()
  await signIn(otherPage)
  await expect(otherPage.getByRole('button', { name: /sign out/i })).toBeVisible()

  // 이 화면을 여는 동안 내 토큰이 한 번 회전한다. 그래서 목록 맨 위는 내
  // 현재 세션이고, 그다음 최신이 방금 들어온 두 번째 기기다.
  await page.goto('/settings/sessions')
  // 사이드바의 "Sign out" 은 나를 로그아웃한다. 기기 목록 안에서만 찾는다.
  const devices = page.getByRole('list', { name: /devices/i })
  const stale = devices.getByRole('button', { name: /^revoke /i })
  await expect(stale.first()).toBeVisible()
  await stale.first().click()
  await expect(page.getByRole('alert')).toHaveCount(0)

  // 끊은 쪽은 즉시 죽는다 — 서버가 세션을 들고 있기 때문이다.
  await otherPage.goto('/projects')
  await expect(otherPage.getByLabel(/email/i)).toBeVisible()
  await other.close()

  // 끊은 사람 자신은 그대로 붙어 있다.
  await expect(page.getByRole('button', { name: /sign out everywhere/i })).toBeVisible()
  await expect(page.getByText('This device', { exact: true })).toBeVisible()

  expect(consoleErrors).toEqual([])
})
