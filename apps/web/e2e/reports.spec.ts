import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

/**
 * 리포트 — IQL 집계 (A29, M5).
 *
 * 브라우저에서 붙잡는 것:
 *
 * - **칸의 합이 총계와 맞는다.** 안 맞으면 사람은 큰 쪽을 믿는다.
 * - **담당자 없음이 하나의 칸으로 보인다.** 보통 가장 봐야 할 무리다.
 * - 숫자에서 목록으로 갈 수 있다 — "40건" 을 보고 "어떤 40건인가" 를 물을
 *   수 없으면 리포트는 막다른 길이다.
 */

test('세면 칸이 보이고, 합이 총계와 맞는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'first')
  await createIssue(page, key, 'second')
  await createIssue(page, key, 'third')

  await page.goto('/reports')
  await page.getByLabel(/query/i).fill(`project = ${key}`)
  await page.getByRole('button', { name: /^count$/i }).click()

  await expect(page.getByText(/3 issues in total/i)).toBeVisible()
  // 세 건이 모두 Open 이므로 칸 하나에 3 이 들어간다 — 합이 총계와 맞는다.
  await expect(page.getByRole('rowheader', { name: 'Open' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '3' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('담당자 없음이 하나의 칸으로 보인다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'nobody has this')

  await page.goto('/reports')
  await page.getByLabel(/query/i).fill(`project = ${key}`)
  await page.getByLabel(/group by/i).selectOption('assignee')
  await page.getByRole('button', { name: /^count$/i }).click()

  // **빈 칸이 아니라 이름이 붙은 칸이다.**
  await expect(page.getByRole('rowheader', { name: /^none$/i })).toBeVisible()
  await expect(page.getByText(/1 issue in total/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('셈에서 목록으로 넘어갈 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'countable thing')

  await page.goto('/reports')
  await page.getByLabel(/query/i).fill(`project = ${key}`)
  await page.getByRole('button', { name: /^count$/i }).click()
  await expect(page.getByText(/1 issue in total/i)).toBeVisible()

  await page.getByRole('link', { name: /open this count as a list/i }).click()
  await page.waitForURL(/\/issues\?/)
  await expect(page.getByText('countable thing')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('모르는 기준은 거절하고, 되는 기준만 고르게 한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'grouped')

  await page.goto('/reports')
  await page.getByLabel(/query/i).fill(`project = ${key}`)
  await page.getByRole('button', { name: /^count$/i }).click()
  await expect(page.getByText(/1 issue in total/i)).toBeVisible()

  // 기준 목록은 **서버가 준 것**이다. `summary` 처럼 못 세는 것은 없다.
  const options = await page.getByLabel(/group by/i).locator('option').allTextContents()
  expect(options).not.toContain('Summary')
  expect(options.length).toBeGreaterThanOrEqual(6)

  expect(consoleErrors).toEqual([])
})
