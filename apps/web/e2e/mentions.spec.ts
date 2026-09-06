import type { Page } from '@playwright/test'

import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

/**
 * 소스 모드의 코멘트 입력칸.
 *
 * 편집기는 서식 모드가 기본이다. 이 스펙이 보는 것은 **소스 모드의** 멘션이라
 * (서식 모드 쪽은 wysiwyg.spec.ts) 탭을 명시적으로 고른다.
 */
async function commentBox(page: Page) {
  const source = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await source.getAttribute('aria-selected')) !== 'true') await source.click()
  return page.getByLabel(/^comments$/i)
}

test('@ 를 치면 사람 목록이 뜨고 고르면 멘션이 들어간다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'mention issue')

  const comment = await commentBox(page)
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

  await (await commentBox(page)).fill('mail me at a@b.com')
  await expect(page.getByRole('listbox', { name: /people to mention/i })).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('키보드로 후보를 고를 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'keyboard mention')

  const comment = await commentBox(page)
  await comment.click()
  await comment.fill('@Admin')
  await expect(page.getByRole('listbox', { name: /people to mention/i })).toBeVisible()
  await comment.press('Enter')

  await expect(comment).toHaveValue(/\[@Administrator\]\(user:[0-9a-f-]+\)/)

  expect(consoleErrors).toEqual([])
})
