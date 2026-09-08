import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

async function createBoard(page: import('@playwright/test').Page, key: string, name: string) {
  await page.goto('/boards')
  await pickProject(page, key)
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

test('스윔레인으로 나누면 컬럼이 레인마다 반복된다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'urgent one', { priority: '1' })
  await createIssue(page, key, 'lazy one', { priority: '5' })

  await createBoard(page, key, 'Lanes')
  // 스윔레인이 없으면 컬럼 머리글 셋뿐이다.
  await expect(page.locator('h2')).toHaveCount(3)

  await page.getByLabel(/swimlanes/i).selectOption('priority')

  // 레인 둘 × 컬럼 셋 + 레인 머리글 둘.
  await expect(page.getByRole('heading', { name: /^Highest/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /^Lowest/ })).toBeVisible()
  await expect(page.locator('h2')).toHaveCount(8)

  // 카드는 자기 레인에만 있다.
  const highest = page.locator('div').filter({ hasText: /^Highest/ }).first()
  await expect(highest.getByText('urgent one')).toBeVisible()

  // 다시 끄면 원래대로.
  await page.getByLabel(/swimlanes/i).selectOption('')
  await expect(page.locator('h2')).toHaveCount(3)

  expect(consoleErrors).toEqual([])
})

test('빈 레인은 만들지 않는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'only medium', { priority: '3' })

  await createBoard(page, key, 'Sparse')
  await page.getByLabel(/swimlanes/i).selectOption('priority')

  // 우선순위는 다섯 단계지만 쓰인 건 하나다. 빈 줄만 늘어서면 더 못 본다.
  await expect(page.getByRole('heading', { name: /^Medium/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /^Highest/ })).toBeHidden()

  expect(consoleErrors).toEqual([])
})
