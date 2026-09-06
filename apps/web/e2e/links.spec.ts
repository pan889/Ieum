/**
 * 이슈↔문서 양방향 링크와 페이지 템플릿.
 *
 * 링크는 중립 표(`entity_link`)에 저장되고 위키가 양쪽을 답한다 — 이슈
 * 모듈이 답하려면 위키를 알아야 하고, 그러면 의존 그래프에 고리가 생긴다.
 * 화면에서만 잡히는 건 두 가지다: `issue:` 링크가 눌러서 실제로 가는지,
 * 그리고 템플릿 본문이 새 문서에 들어가는지.
 */

import type { Page } from '@playwright/test'

import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

function spaceKey(): string {
  return 'L' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

async function createSpace(page: Page, key: string): Promise<void> {
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  await page.getByLabel(/find a space/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
}

async function writeDoc(page: Page, key: string, title: string, body: string): Promise<void> {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill(body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
}

test('문서가 이슈를 언급하면 이슈 쪽에도 뜬다', async ({ page, consoleErrors }) => {
  const project = projectKey()
  await signIn(page)
  await createProject(page, project)
  const issueKey = await createIssue(page, project, 'Issue that docs mention')

  const space = spaceKey()
  await createSpace(page, space)
  await writeDoc(page, space, 'Runbook', `관련: [${issueKey}](issue:${issueKey})`)

  await page.goto(`/issues/${issueKey}`)
  const panel = page.getByText(/mentioned in docs/i).locator('..')
  await expect(panel.getByRole('link', { name: /Runbook/ })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('`issue:` 링크를 누르면 그 이슈로 간다', async ({ page, consoleErrors }) => {
  const project = projectKey()
  await signIn(page)
  await createProject(page, project)
  const issueKey = await createIssue(page, project, 'Jump target')

  const space = spaceKey()
  await createSpace(page, space)
  await writeDoc(page, space, 'Jumping', `[${issueKey}](issue:${issueKey}) 로 간다.`)

  // 스킴 그대로 두면 눌러도 아무 데도 안 간다.
  await page.getByRole('link', { name: issueKey, exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/issues/${issueKey}$`))
  await expect(page.getByRole('heading', { name: 'Jump target' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('본문에서 링크를 지우면 이슈 쪽에서도 사라진다', async ({ page, consoleErrors }) => {
  const project = projectKey()
  await signIn(page)
  await createProject(page, project)
  const issueKey = await createIssue(page, project, 'Unlinked later')

  const space = spaceKey()
  await createSpace(page, space)
  await writeDoc(page, space, 'Fickle', `[${issueKey}](issue:${issueKey})`)

  await page.goto(`/issues/${issueKey}`)
  await expect(page.getByText(/mentioned in docs/i)).toBeVisible()

  await page.goto(`/wiki/${space}/fickle`)
  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill('이제 링크가 없다.')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()

  await page.goto(`/issues/${issueKey}`)
  // 자리를 차지하지 않는다. 대부분의 이슈에는 링크한 문서가 없다.
  await expect(page.getByText(/mentioned in docs/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('템플릿을 만들어 새 문서의 뼈대로 쓴다', async ({ page, consoleErrors }) => {
  const space = spaceKey()
  await signIn(page)
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)

  await page.getByRole('button', { name: /^templates$/i }).click()
  await page.getByRole('button', { name: /new template/i }).click()
  await page.getByLabel(/^template name$/i).fill('Meeting')
  await page.getByLabel(/^template body$/i).fill('## Attendees\n\n## Decisions')
  await page.getByRole('button', { name: /save template/i }).click()
  await expect(page.getByText('Meeting')).toBeVisible()

  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Weekly sync')
  await page.getByLabel(/start from/i).selectOption({ label: 'Meeting' })
  await page.getByRole('button', { name: /^create$/i }).click()

  // 뼈대를 매번 손으로 옮겨 적게 하면 결국 아무도 규격을 안 지킨다.
  await expect(page.getByRole('heading', { name: 'Attendees' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Decisions' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('템플릿을 고르지 않으면 빈 문서다', async ({ page, consoleErrors }) => {
  const space = spaceKey()
  await signIn(page)
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)

  await page.getByRole('button', { name: /^templates$/i }).click()
  await page.getByRole('button', { name: /new template/i }).click()
  await page.getByLabel(/^template name$/i).fill('Meeting')
  await page.getByLabel(/^template body$/i).fill('## Attendees')
  await page.getByRole('button', { name: /save template/i }).click()
  await expect(page.getByText('Meeting')).toBeVisible()

  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Empty one')
  await page.getByRole('button', { name: /^create$/i }).click()

  await expect(page.getByRole('heading', { name: 'Attendees' })).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})
