import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

/**
 * 간트 (A17, M5).
 *
 * 브라우저에서 붙잡는 것:
 *
 * - **어긋난 순서를 눈에 띄게 적는다.** 겹친 막대만 그려 놓으면 사람은
 *   화살표가 있으니 순서가 지켜진다고 읽는다.
 * - 순서가 맞으면 아무 말도 안 한다 — 늘 붉으면 아무도 안 본다.
 */

function dayThisMonth(day: number): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `${String(now.getFullYear())}-${month}-${String(day).padStart(2, '0')}`
}

async function setDates(
  page: import('@playwright/test').Page,
  issueKey: string,
  start: string,
  due: string,
) {
  await page.goto(`/issues/${issueKey}`)
  await page.getByLabel(/^start$/i).fill(start)
  await expect(page.getByLabel(/^start$/i)).toHaveValue(start)
  await page.getByLabel(/^due$/i).fill(due)
  await expect(page.getByLabel(/^due$/i)).toHaveValue(due)
}

async function precede(
  page: import('@playwright/test').Page,
  first: string,
  then: string,
) {
  await page.goto(`/issues/${first}`)
  await page.getByRole('button', { name: /link an issue/i }).click()
  await page.getByLabel(/^relation$/i).selectOption({ label: 'precedes' })
  await page.getByLabel(/^issue key$/i).fill(then)
  await page.getByRole('button', { name: /^link$/i }).click()
  await expect(page.getByRole('link', { name: then })).toBeVisible()
}

test('순서가 어긋난 선후 관계를 간트가 적어 준다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const first = await createIssue(page, key, 'comes first')
  const then = await createIssue(page, key, 'comes after')

  // 뒤에 와야 하는 일이 앞선 일이 끝나기 전에 시작한다 — 일정 충돌이다.
  await setDates(page, first, dayThisMonth(5), dayThisMonth(12))
  await setDates(page, then, dayThisMonth(9), dayThisMonth(16))
  await precede(page, first, then)

  await page.goto('/gantt')
  await pickProject(page, key)

  // **막대만 그리고 넘어가지 않는다.**
  await expect(page.getByText(/1 dependency is out of order/i)).toBeVisible()
  await expect(page.getByText(new RegExp(`${then} follows ${first}`))).toBeVisible()
  await expect(page.getByText(/4 days overlap/i)).toBeVisible()
  // 양쪽 막대에 표시가 붙는다 — 후행만 칠하면 "이 일이 늦었다" 로 읽힌다.
  await expect(page.getByText(/out of order/i)).toHaveCount(3)

  expect(consoleErrors).toEqual([])
})

test('순서가 맞으면 경고하지 않는다 — 늘 붉으면 아무도 안 본다', async ({
  page,
  consoleErrors,
}) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const first = await createIssue(page, key, 'comes first')
  const then = await createIssue(page, key, 'comes after')

  await setDates(page, first, dayThisMonth(5), dayThisMonth(8))
  await setDates(page, then, dayThisMonth(9), dayThisMonth(12))
  await precede(page, first, then)

  await page.goto('/gantt')
  await pickProject(page, key)

  await expect(page.getByText(/out of order/i)).toHaveCount(0)
  // 의존은 그대로 있다 — 충돌이 없다는 것이 관계가 없다는 뜻은 아니다.
  await expect(page.getByText(/1 predecessor/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('날짜 없는 이슈는 막대가 없고 몇 건인지 적힌다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const dated = await createIssue(page, key, 'on the chart')
  await createIssue(page, key, 'nowhere to draw')
  await setDates(page, dated, dayThisMonth(4), dayThisMonth(6))

  await page.goto('/gantt')
  await pickProject(page, key)

  await expect(page.getByRole('link', { name: new RegExp(dated) })).toBeVisible()
  await expect(page.getByText(/1 issue has no dates and can't be drawn/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
