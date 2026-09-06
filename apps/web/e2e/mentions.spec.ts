import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

test('@ 를 치면 사람 목록이 뜨고 고르면 멘션이 들어간다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'mention issue')

  const comment = page.getByLabel(/^comments$/i)
  await comment.fill('cc @')

  const list = page.getByRole('listbox', { name: /people to mention/i })
  await expect(list).toBeVisible()
  await list.getByRole('option').first().click()

  // 마크다운 멘션 링크가 본문에 들어가야 한다. 표시 이름이 아니라 id 를
  // 담으므로 이름이 바뀌어도 안 깨진다.
  await expect(comment).toHaveValue(/cc \[@Administrator\]\(user:[0-9a-f-]+\)/)

  await page.getByRole('button', { name: /^comment$/i }).click()
  // 링크가 아니라 칩으로 그린다 — 눌러도 갈 데가 없다.
  await expect(page.locator('.ieum-mention')).toHaveText('@Administrator')
  await expect(page.locator('a[href^="user:"]')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('이메일을 칠 때는 목록이 뜨지 않는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'email issue')

  await page.getByLabel(/^comments$/i).fill('mail me at a@b.com')
  await expect(page.getByRole('listbox', { name: /people to mention/i })).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('키보드로 후보를 고를 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'keyboard mention')

  const comment = page.getByLabel(/^comments$/i)
  await comment.click()
  await comment.fill('@Admin')
  await expect(page.getByRole('listbox', { name: /people to mention/i })).toBeVisible()
  await comment.press('Enter')

  await expect(comment).toHaveValue(/\[@Administrator\]\(user:[0-9a-f-]+\)/)

  expect(consoleErrors).toEqual([])
})
