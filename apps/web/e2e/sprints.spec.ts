import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

/**
 * 스프린트와 번다운 (M5).
 *
 * 브라우저에서 붙잡는 것:
 *
 * - **닫을 때 어디로 보낼지 고르는 길이 화면에 있다.** 서버가 거절하는 것만
 *   있고 고르게 하는 쪽이 없으면, 사람은 에러만 보고 만다.
 * - **보드가 무엇으로 걸렀는지 말한다.** 이번 스프린트만 보여 주는데 그
 *   사실을 안 적으면, 백로그에 쌓인 일이 안 보이는 것과 아예 없는 것을
 *   구분할 수 없다.
 */

async function createSprint(page: import('@playwright/test').Page, name: string) {
  await page.getByLabel(/^name$/i).fill(name)
  await page.getByRole('button', { name: /create sprint/i }).click()
  await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
}

test('스프린트를 만들고 이슈를 넣고 시작한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'sprint work')

  await page.goto('/sprints')
  await pickProject(page, key)
  await expect(page.getByText(/no sprints yet/i)).toBeVisible()

  await createSprint(page, 'Cycle 1')
  await expect(page.getByText(/^planned$/i)).toBeVisible()

  // 백로그에서 골라 넣는다.
  await page.getByLabel(/sprint work/).check()
  await page.getByLabel(/^sprint$/i).selectOption({ label: 'Cycle 1' })
  await page.getByRole('button', { name: /add to sprint/i }).click()
  // 넣으면 백로그가 빈다 — 그게 "들어갔다" 의 증거다.
  await expect(page.getByText(/the backlog is empty/i)).toBeVisible()

  await page.getByRole('button', { name: /^start$/i }).click()
  await expect(page.getByText(/^running$/i)).toBeVisible()

  // 시작하면 그날 점이 찍힌다. 되짚어 계산하지 않으므로, 안 찍혔으면 빈다.
  await page.getByRole('button', { name: 'Cycle 1', exact: true }).click()
  await expect(page.getByRole('img', { name: /burndown/i })).toBeVisible()
  await expect(page.getByText(/no points recorded yet/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('닫을 때 남은 것을 어디로 보낼지 화면이 고르게 한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'unfinished thing')

  await page.goto('/sprints')
  await pickProject(page, key)
  await createSprint(page, 'Cycle 1')
  await page.getByLabel(/unfinished thing/).check()
  await page.getByLabel(/^sprint$/i).selectOption({ label: 'Cycle 1' })
  await page.getByRole('button', { name: /add to sprint/i }).click()
  await page.getByRole('button', { name: /^start$/i }).click()
  await expect(page.getByText(/^running$/i)).toBeVisible()

  await page.getByRole('button', { name: /^close$/i }).first().click()
  const form = page.getByRole('combobox', { name: /choose where they go/i })
  await expect(form).toBeVisible()
  // **고르기 전에는 못 닫는다.** 기본값이 있으면 조용히 백로그로 간다.
  await expect(page.getByRole('button', { name: /^close$/i }).last()).toBeDisabled()

  await form.selectOption('backlog')
  await page.getByRole('button', { name: /^close$/i }).last().click()
  await expect(page.getByText(/^closed$/i)).toBeVisible()

  // 남은 이슈는 사라지지 않고 백로그로 돌아왔다.
  await expect(page.getByLabel(/unfinished thing/)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('보드는 도는 스프린트만 보여 주고, 그 사실을 적는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'in the sprint')
  await createIssue(page, key, 'left in backlog')

  // 스프린트를 만들고 한 건만 넣고 시작한다.
  await page.goto('/sprints')
  await pickProject(page, key)
  await createSprint(page, 'Cycle 1')
  await page.getByLabel(/in the sprint/).check()
  await page.getByLabel(/^sprint$/i).selectOption({ label: 'Cycle 1' })
  await page.getByRole('button', { name: /add to sprint/i }).click()
  await page.getByRole('button', { name: /^start$/i }).click()
  await expect(page.getByText(/^running$/i)).toBeVisible()

  await page.goto('/boards')
  await pickProject(page, key)
  await page.getByLabel(/board name/i).fill('Sprint board')
  await page.getByRole('button', { name: /create board/i }).click()
  await page.getByRole('link', { name: /open board/i }).click()
  await page.waitForURL(/\/boards\/[0-9a-f-]+$/)

  // **무엇으로 걸렀는지 적혀 있다.**
  await expect(page.getByText(/shows only sprint “Cycle 1”/i)).toBeVisible()
  await expect(page.getByText('in the sprint')).toBeVisible()
  await expect(page.getByText('left in backlog')).toHaveCount(0)

  // 전부 보기로 바꾸면 백로그도 올라온다.
  await page.getByLabel(/^show$/i).selectOption('all')
  await expect(page.getByText('left in backlog')).toBeVisible()
  await expect(page.getByText(/shows only sprint/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('이슈 상세가 자기 스프린트를 말하고, 거기서 바꿀 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const issueKey = await createIssue(page, key, 'needs a home')

  await page.goto('/sprints')
  await pickProject(page, key)
  await createSprint(page, 'Cycle 1')

  // **이슈에서 자기 스프린트가 보여야 한다.** 스프린트 화면에서만 보이면
  // "이 이슈가 이번 주기 일인가" 를 이슈를 보면서 답할 수 없다.
  await page.goto(`/issues/${issueKey}`)
  const picker = page.getByRole('combobox', { name: /^sprints$/i })
  await expect(picker).toBeVisible()
  // 아직 백로그다.
  await expect(picker).toHaveValue('')

  await picker.selectOption({ label: 'Cycle 1' })
  await expect(page.getByRole('option', { name: 'Cycle 1', selected: true })).toBeAttached()

  // 스프린트 화면에서도 같은 답이 나온다 — 백로그가 비었다.
  await page.goto('/sprints')
  await pickProject(page, key)
  await expect(page.getByText(/the backlog is empty/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
