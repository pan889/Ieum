/**
 * 위키. 스페이스 → 트리 → 문서 보기·편집·이력.
 *
 * 문서 주소는 URL 이 소유한다(`/wiki/ENG/deploy/rollback`). 링크 하나로 같은
 * 문서가 열려야 하는데, 이건 라우터·splat 파싱이 얽혀 있어 브라우저 없이는
 * 확인할 수 없다.
 */

import { expect, signIn, test } from './fixtures'

function spaceKey(): string {
  return 'W' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

async function createSpace(page: import('@playwright/test').Page, key: string): Promise<void> {
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  await page.getByLabel(/find a space/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
}

async function createPage(
  page: import('@playwright/test').Page,
  title: string,
): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

test('스페이스를 만들고 문서를 쓴다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Deploy Runbook')
  // 문서 주소가 URL 에 실린다.
  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/deploy-runbook$`))

  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill('# Steps\n\n1. build\n2. deploy')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 마크다운이 렌더된다 — 원문이 그대로 보이면 안 된다.
  await expect(page.getByRole('heading', { name: 'Steps' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('링크를 그대로 열면 같은 문서가 나온다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Shared Doc')

  // 주소창에 직접 친 것과 같다.
  await page.goto(`/wiki/${key}/shared-doc`)
  await expect(page.getByRole('heading', { name: 'Shared Doc' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('하위 문서를 만들면 경로가 이어진다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Parent')

  await page.getByRole('link', { name: 'Parent' }).hover()
  await page.getByRole('button', { name: /new page under parent/i }).click()
  await page.getByLabel(/^title$/i).fill('Child')
  await page.getByRole('button', { name: /^create$/i }).click()

  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/parent/child$`))
  // 빵부스러기에 조상이 뜬다.
  await expect(page.getByRole('navigation', { name: /page location/i })).toContainText('Parent')

  expect(consoleErrors).toEqual([])
})

test('편집하면 새 판이 쌓이고 복원해도 이력이 남는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Versioned')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill('first draft')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('first draft')).toBeVisible()

  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill('second draft')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('second draft')).toBeVisible()

  await page.getByRole('button', { name: /^history$/i }).click()
  // 머리글에도 판 번호가 있으므로 이력 목록 안으로 좁힌다.
  const history = page.getByRole('list').filter({ hasText: 'v1' })
  // v1(빈 본문) + v2 + v3.
  await expect(history.getByText('v3', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /^restore$/i }).first().click()
  // 되감지 않고 새 판을 만든다 — 이력은 계속 늘어난다.
  await expect(history.getByText('v4', { exact: true })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('트리를 접었다 펼 수 있다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Top')

  await page.getByRole('link', { name: 'Top' }).hover()
  await page.getByRole('button', { name: /new page under top/i }).click()
  await page.getByLabel(/^title$/i).fill('Nested')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('link', { name: 'Nested' })).toBeVisible()

  await page.getByRole('button', { name: /collapse top/i }).click()
  await expect(page.getByRole('link', { name: 'Nested' })).toBeHidden()

  await page.getByRole('button', { name: /expand top/i }).click()
  await expect(page.getByRole('link', { name: 'Nested' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('휴지통으로 보내면 트리에서 빠진다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Temporary')

  await page.getByRole('button', { name: /move to trash/i }).click()
  await expect(page.getByRole('link', { name: 'Temporary' })).toBeHidden()

  expect(consoleErrors).toEqual([])
})
