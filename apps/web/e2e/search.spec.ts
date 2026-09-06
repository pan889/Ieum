/**
 * 통합 검색 (ADR-0005).
 *
 * 색인은 원본과 같은 트랜잭션에서 갱신된다. 그래서 방금 만든 이슈·문서가
 * **곧바로** 검색돼야 한다 — 워커를 기다리는 구조라면 여기서 드러난다.
 */

import type { Page } from '@playwright/test'

import { createIssue, createProject, createSpace, expect, projectKey, signIn, test, uniqueKey, writeBody } from './fixtures'

function unique(prefix: string): string {
  return prefix + Math.random().toString(36).slice(2, 8)
}

function spaceKey(): string {
  return uniqueKey('F')
}

async function findEverything(page: Page, query: string): Promise<void> {
  await page.getByRole('searchbox', { name: /search/i }).fill(query)
  await page.getByRole('searchbox', { name: /search/i }).press('Enter')
  await page.waitForURL(/\/search\?/)
}

/** 결과 목록. 사이드바 메뉴도 목록이라 이름으로 좁힌다. */
function hits(page: Page) {
  return page.getByRole('list', { name: /results/i }).getByRole('listitem')
}

async function writeDoc(page: Page, key: string, title: string, body: string): Promise<void> {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
}

test('방금 만든 이슈가 곧바로 검색된다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  const word = unique('needle')
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, `Find me ${word}`)

  await findEverything(page, word)

  const hit = hits(page).filter({ hasText: word })
  await expect(hit).toHaveCount(1)
  await expect(hit.getByText('Issue', { exact: true })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('문서와 이슈가 한 목록에 섞여 나온다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  const word = unique('shared')
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, `Issue about ${word}`)

  const space = spaceKey()
  await createSpace(page, space)
  await writeDoc(page, space, 'Doc side', `문서에도 ${word} 가 있다.`)

  await findEverything(page, word)

  // 종류 탭에도 같은 글자가 있다. 결과 목록 안으로 좁힌다.
  await expect(hits(page).getByText('Issue', { exact: true })).toHaveCount(1)
  await expect(hits(page).getByText('Doc', { exact: true })).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})

test('한글을 일부만 쳐도 찾는다', async ({ page, consoleErrors }) => {
  const space = spaceKey()
  await signIn(page)
  await createSpace(page, space)
  await writeDoc(page, space, `한글 문서 ${space}`, '마이그레이션을 검토한다.')

  // 형태소 분석이 되는지. 이게 PGroonga 를 고른 이유다.
  await findEverything(page, '마이그레')
  await expect(hits(page).first()).toContainText('마이그레이션')

  expect(consoleErrors).toEqual([])
})

test('종류 탭으로 좁히면 URL 에 실린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  const word = unique('narrow')
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, `Issue ${word}`)

  const space = spaceKey()
  await createSpace(page, space)
  await writeDoc(page, space, `Doc ${word}`, `본문에 ${word}.`)

  await findEverything(page, word)
  await page.getByRole('button', { name: 'Doc', exact: true }).click()
  await expect(page).toHaveURL(/kind=page/)
  await expect(hits(page).getByText('Issue', { exact: true })).toHaveCount(0)

  // 링크를 그대로 열면 같은 결과가 나온다.
  const url = page.url()
  await page.goto(url)
  await expect(hits(page).getByText('Issue', { exact: true })).toHaveCount(0)
  await expect(hits(page).filter({ hasText: word })).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})

test('고친 내용으로 다시 찾힌다', async ({ page, consoleErrors }) => {
  const space = spaceKey()
  const before = unique('before')
  const after = unique('after')
  await signIn(page)
  await createSpace(page, space)
  await writeDoc(page, space, `Edited ${space}`, `처음에는 ${before} 라고 썼다.`)

  await page.goto(`/wiki/${space}`)
  await page.getByRole('link', { name: `Edited ${space}` }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, `이제는 ${after} 라고 쓴다.`)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()

  await findEverything(page, after)
  await expect(hits(page).filter({ hasText: after })).toHaveCount(1)

  // 옛 본문으로는 더 이상 찾히지 않는다.
  await findEverything(page, before)
  await expect(hits(page)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('휴지통으로 보내면 검색에서 빠진다', async ({ page, consoleErrors }) => {
  const space = spaceKey()
  const word = unique('trashed')
  await signIn(page)
  await createSpace(page, space)
  await writeDoc(page, space, `Trash ${space}`, `여기 ${word} 가 있다.`)

  await findEverything(page, word)
  await expect(hits(page)).toHaveCount(1)

  await page.goto(`/wiki/${space}`)
  await page.getByRole('link', { name: `Trash ${space}` }).click()
  await page.getByRole('button', { name: /move to trash/i }).click()
  await expect(page.getByRole('link', { name: `Trash ${space}` })).toBeHidden()

  // 열 수 없는 결과가 목록에 남으면 안 된다.
  await findEverything(page, word)
  await expect(hits(page)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('찾는 것이 없으면 그렇게 말한다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await findEverything(page, unique('nothingmatches'))
  await expect(page.getByText(/nothing matches/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
