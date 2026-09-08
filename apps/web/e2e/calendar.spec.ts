import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

/**
 * 달력 (A18, M5).
 *
 * 브라우저에서 붙잡는 것:
 *
 * - **날짜가 없어 못 놓은 이슈를 화면이 적는다.** 조용히 빼면 사람은 "이번
 *   달은 한가하다" 고 읽는데, 실제로는 날짜만 안 적힌 일이 있다.
 * - **날짜를 고칠 수 있다.** 만들 때만 적을 수 있으면 달력은 대부분 비어
 *   있고, 잘못 적은 날은 영영 잘못 적힌 채로 남는다.
 */

/** 이 달의 어느 날. 달을 넘나드는 이동 없이 보이는 자리에 둔다. */
function dayThisMonth(day: number): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `${String(now.getFullYear())}-${month}-${String(day).padStart(2, '0')}`
}

async function setDates(
  page: import('@playwright/test').Page,
  issueKey: string,
  dates: { start?: string; due?: string },
) {
  await page.goto(`/issues/${issueKey}`)
  if (dates.start !== undefined) {
    await page.getByLabel(/^start$/i).fill(dates.start)
    await expect(page.getByLabel(/^start$/i)).toHaveValue(dates.start)
  }
  if (dates.due !== undefined) {
    await page.getByLabel(/^due$/i).fill(dates.due)
    await expect(page.getByLabel(/^due$/i)).toHaveValue(dates.due)
  }
}

test('달력이 날짜 있는 일을 놓고, 못 놓은 것은 몇 건인지 적는다', async ({
  page,
  consoleErrors,
}) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const dated = await createIssue(page, key, 'has a date')
  await createIssue(page, key, 'no date at all')

  // **날짜는 상세에서 고친다.** 만들 때만 적을 수 있으면 달력을 채울 길이 없다.
  await setDates(page, dated, { start: dayThisMonth(10), due: dayThisMonth(14) })

  await page.goto('/calendar')
  await pickProject(page, key)

  // 놓인 것은 달력에 있다. **주를 넘는 띠는 줄마다 잘려 조각이 여럿**이라
  // `.first()` 로 본다 — 조각 수는 그 달 1일의 요일에 달려 있어서 여기서
  // 못 박을 값이 아니다(`grid.test.ts` 가 날짜를 고정해 놓고 본다).
  await expect(page.getByRole('link', { name: new RegExp(dated) }).first()).toBeVisible()
  // **못 놓은 것은 숫자로 남는다.** 이걸 안 적으면 화면은 거짓말을 한다.
  await expect(page.getByText(/1 issue has no dates/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('고친 날짜가 달력에 그대로 반영된다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const issue = await createIssue(page, key, 'moves around')

  await setDates(page, issue, { due: dayThisMonth(5) })
  await page.goto('/calendar')
  await pickProject(page, key)
  await expect(page.getByRole('link', { name: new RegExp(issue) }).first()).toBeVisible()
  await expect(page.getByText(/no dates/i)).toHaveCount(0)

  // 날짜를 지우면 달력에서 빠지고, **빠졌다는 사실이 적힌다.**
  await page.goto(`/issues/${issue}`)
  await page.getByLabel(/^due$/i).fill('')
  await expect(page.getByLabel(/^due$/i)).toHaveValue('')

  await page.goto('/calendar')
  await pickProject(page, key)
  await expect(page.getByRole('link', { name: new RegExp(issue) })).toHaveCount(0)
  await expect(page.getByText(/1 issue has no dates/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('좁히기 질의가 달력과 못 놓은 숫자에 함께 걸린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const urgent = await createIssue(page, key, 'urgent thing', { priority: '1' })
  const calm = await createIssue(page, key, 'calm thing', { priority: '5' })
  await setDates(page, urgent, { due: dayThisMonth(8) })
  await setDates(page, calm, { due: dayThisMonth(9) })

  await page.goto('/calendar')
  await pickProject(page, key)
  await expect(page.getByRole('link', { name: new RegExp(calm) }).first()).toBeVisible()

  await page.getByLabel(/narrow/i).fill('priority = 1')
  await page.getByRole('button', { name: /^apply$/i }).click()

  await expect(page.getByRole('link', { name: new RegExp(urgent) }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: new RegExp(calm) })).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})
