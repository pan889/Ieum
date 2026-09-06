/**
 * 디렉티브 = 매크로 (wiki-markdown.md 3절).
 *
 * 브라우저에서만 잡히는 것들이 있다: 문서를 조각으로 나눠 그리므로 제목 `id`
 * 번호가 조각을 넘어 이어져야 목차 링크가 맞고, `::issues` 는 서버 질의를
 * 실제로 돌려야 결과가 나온다.
 */

import { bodyField, createIssue, createProject, createSpace, expect, projectKey, signIn, test, writeBody } from './fixtures'
import type { Page } from '@playwright/test'

function spaceKey(): string {
  return 'D' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

async function writePage(page: Page, title: string, body: string): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
}

test(':::info 는 강조 상자로 그려진다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Boxes', ':::warning\n**조심**하세요.\n\n- 하나\n- 둘\n:::')

  // 원문이 그대로 보이면 안 된다.
  await expect(page.getByText(':::warning')).toBeHidden()
  const box = page.locator('.ieum-admonition')
  await expect(box).toBeVisible()
  await expect(box).toContainText('Warning')
  await expect(box.getByRole('listitem').first()).toHaveText('하나')

  // 닫는 `:::` 이 마지막 목록 항목으로 빨려 들어가면 안 된다 (정규화 회귀).
  await page.reload()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(await bodyField(page)).toHaveValue(
    ':::warning\n**조심**하세요.\n\n- 하나\n- 둘\n:::',
  )

  expect(consoleErrors).toEqual([])
})

test('::toc 는 문서 제목으로 목차를 만든다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Contents', '::toc{depth=2}\n\n## 준비\n\n본문\n\n## 배포\n\n본문\n\n### 자세히')

  const toc = page.getByRole('navigation', { name: /on this page/i })
  await expect(toc.getByRole('link', { name: '준비' })).toBeVisible()
  // depth=2 라 `###` 은 빠진다.
  await expect(toc.getByRole('link', { name: '자세히' })).toHaveCount(0)

  // 링크가 실제 제목을 가리킨다 — 조각으로 나눠 그려도 id 가 맞아야 한다.
  await expect(toc.getByRole('link', { name: '배포' })).toHaveAttribute('href', '#배포')
  await expect(page.locator('h2#배포')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::children 은 하위 문서를 나열한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Parent', '::children')
  await page.getByRole('link', { name: 'Parent' }).hover()
  await page.getByRole('button', { name: /new page under parent/i }).click()
  await page.getByLabel(/^title$/i).fill('Child One')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: 'Child One' })).toBeVisible()

  // 트리와 빵부스러기 양쪽에 'Parent' 링크가 있다. 주소로 간다.
  await page.goto(`/wiki/${key}/parent`)
  const list = page.getByRole('navigation', { name: /child pages/i })
  await expect(list.getByRole('link', { name: 'Child One' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::children 은 이슈 설명에서는 쓸 수 없다고 말한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'Directive out of place')

  await page.getByRole('button', { name: /^edit$/i }).first().click()
  await writeBody(page, '::children')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 조용히 비워 두면 왜 안 나오는지 알 수 없다.
  await expect(page.getByText(/only works inside a wiki page/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::issues 는 IQL 결과를 표로 그린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'Directive target')

  const space = spaceKey()
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await writePage(
    page,
    'Dashboard',
    `::issues{query="project = ${key}" columns="key,summary,status"}`,
  )

  const table = page.getByRole('table')
  await expect(table.getByRole('cell', { name: 'Directive target' })).toBeVisible()
  await expect(table.getByRole('link', { name: `${key}-1` })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('읽을 수 없는 매크로는 저장을 막는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Broken')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '::toc{depth=99}')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 보는 시점에 처음 알면 쓴 사람은 이미 떠나고 없다.
  await expect(page.getByRole('alert')).toContainText(/depth/i)

  // 저장이 막힌 것은 의도한 422 다.
  expect(consoleErrors.filter((e) => !e.startsWith('422'))).toEqual([])
})

test('::excerpt 는 다른 문서의 앞부분을 끌어온다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await writePage(
    page,
    'Deploy policy',
    '# 배포 정책\n\n운영 배포는 화요일과 목요일에만 한다.\n\n두 번째 문단.',
  )
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', `::excerpt{page="${key}/deploy-policy"}`)

  // 제목은 원문으로 가는 링크다.
  await expect(page.getByRole('link', { name: 'Deploy policy' }).last()).toBeVisible()
  // 앞부분만 끌어온다. 문서 전체를 박아 넣으면 인용이 아니라 복사다.
  await expect(page.getByText('운영 배포는 화요일과 목요일에만 한다.')).toBeVisible()
  await expect(page.getByText('두 번째 문단.')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('::excerpt 는 없는 문서를 지어내지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Broken', `::excerpt{page="${key}/nope"}`)

  // "권한이 없다" 라고 말하지 않는다 — 그 문서가 있다는 뜻이 되어 버린다.
  await expect(page.getByText(/doesn't exist, or you can't see it/i)).toBeVisible()

  // 없는 문서를 물어본 404 는 의도한 것이다.
  expect(consoleErrors.filter((e) => !e.startsWith('404'))).toEqual([])
})

test('page 없는 ::excerpt 는 저장을 막는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Needs page')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '::excerpt')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 보는 시점에 처음 알면 쓴 사람은 이미 떠나고 없다.
  await expect(page.getByText(/needs a page/i)).toBeVisible()

  // 저장이 막힌 것은 의도한 422 다.
  expect(consoleErrors.filter((e) => !e.startsWith('422'))).toEqual([])
})
