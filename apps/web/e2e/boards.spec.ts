import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

async function createBoard(page: import('@playwright/test').Page, key: string, name: string) {
  await page.goto('/boards')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
  await page.getByLabel(/board name/i).fill(name)
  await page.getByRole('button', { name: /create board/i }).click()
  await page.getByRole('link', { name: /open board/i }).click()
  await page.waitForURL(/\/boards\/[0-9a-f-]+$/)
}

test('보드를 만들고 카드를 옮긴다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'board card')

  await createBoard(page, key, 'Main')
  await expect(page.getByText('board card')).toBeVisible()

  // 기본 컬럼 세 개. 새 이슈는 To Do 에 있어야 한다.
  const columns = page.locator('h2')
  await expect(columns).toHaveCount(3)
  const todo = page.locator('div').filter({ hasText: /^To Do/ }).first()
  await expect(todo.getByText('board card')).toBeVisible()

  // 드래그는 키보드로 못 쓴다. 같은 일을 하는 버튼이 항상 있어야 한다.
  await page.getByRole('button', { name: /move to/i }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: /start progress/i }).click()

  const doing = page.locator('div').filter({ hasText: /^In Progress/ }).first()
  await expect(doing.getByText('board card')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('WIP 제한을 넘기면 컬럼이 알려준다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  // 기본 In Progress 컬럼의 WIP 는 5 다. 여섯 장을 넣어 넘긴다.
  for (let i = 0; i < 6; i += 1) {
    const issueKey = await createIssue(page, key, `wip ${String(i)}`)
    await page.goto(`/issues/${issueKey}`)
    await page.getByRole('button', { name: /start progress/i }).click()
    await expect(page.getByText('In Progress').first()).toBeVisible()
  }

  await createBoard(page, key, 'WIP board')
  const header = page.locator('h2', { hasText: 'In Progress' }).locator('+ span')
  await expect(header).toHaveText(/6 cards/)
  await expect(header).toHaveClass(/text-danger/)

  expect(consoleErrors).toEqual([])
})
