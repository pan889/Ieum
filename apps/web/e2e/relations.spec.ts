import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

test('이슈를 연결하면 양쪽 방향 문구가 다르게 보인다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const blocker = await createIssue(page, key, 'the blocker')
  const blocked = await createIssue(page, key, 'the blocked')

  await page.goto(`/issues/${blocker}`)
  await page.getByRole('button', { name: /link an issue/i }).click()
  await page.getByLabel(/^relation$/i).selectOption({ label: 'blocks' })
  await page.getByLabel(/^issue key$/i).fill(blocked)
  await page.getByRole('button', { name: /^link$/i }).click()

  // 출발점에서는 "blocks".
  await expect(page.getByText('blocks', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: blocked })).toBeVisible()

  // 반대편에서는 "is blocked by" 여야 한다. 같은 문구를 쓰면 방향을 못 읽는다.
  await page.goto(`/issues/${blocked}`)
  await expect(page.getByText('is blocked by')).toBeVisible()
  await expect(page.getByRole('link', { name: blocker })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('하위 이슈 진행률이 부모로 올라간다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  const parent = await createIssue(page, key, 'parent issue')

  await page.goto(`/issues/${parent}`)
  await expect(page.getByText(/no relations yet/i)).toBeVisible()

  // 상세 화면의 "하위 이슈 만들기" 가 상위 이슈를 미리 채워 준다.
  for (const label of ['first child', 'second child']) {
    await page.goto(`/issues/${parent}`)
    await page.getByRole('link', { name: /new sub-issue/i }).click()
    await expect(page.getByLabel(/^parent issue$/i)).toHaveValue(parent)
    await pickProject(page, key)
    await page.getByLabel(/^summary$/i).fill(label)
    await page.getByRole('button', { name: /create issue/i }).click()
    await page.waitForURL(new RegExp(`/issues/${key}-`))
  }

  await page.goto(`/issues/${parent}`)
  await expect(page.getByText('first child')).toBeVisible()
  await expect(page.getByText('second child')).toBeVisible()
  // 자식이 생겼으므로 부모 진행률은 계산값이다.
  await expect(page.getByText('0%')).toBeVisible()

  // 자식 하나를 Resolve 하면 후처리가 100 으로 만들고, 부모는 50 이 된다.
  await page.getByRole('link', { name: `${key}-2` }).click()
  await page.getByRole('button', { name: /start progress/i }).click()
  await page.getByRole('button', { name: /^resolve$/i }).click()

  await page.goto(`/issues/${parent}`)
  await expect(page.getByText('50%')).toBeVisible()

  expect(consoleErrors).toEqual([])
})
