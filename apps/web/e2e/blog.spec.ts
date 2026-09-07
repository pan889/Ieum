/**
 * 스페이스 블로그 (B13). 뉴스·공지가 여기로 들어온다 (A23).
 *
 * 트리에 안 들어간다 — 날짜순으로 흐르는 글이라 위치가 아니라 시간이 자리를
 * 정한다. 그래도 문서와 **같은 것**이라, 판 이력·코멘트·검색이 그대로 붙는지가
 * 여기서 볼 것이다. 별도 표를 만들지 않은 이유가 그것이다.
 */

import { createSpace, expect, signIn, test, uniqueKey, writeBody } from './fixtures'

function spaceKey(): string {
  return uniqueKey('K')
}

async function writePost(
  page: import('@playwright/test').Page,
  title: string,
  body?: string,
): Promise<void> {
  await page.getByRole('link', { name: /^blog$/i }).click()
  await page.getByRole('button', { name: /write a post/i }).click()
  await page.getByLabel(/post title/i).fill(title)
  await page.getByRole('button', { name: /^publish$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  if (body === undefined) return
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.locator('.ieum-markdown').first()).toBeVisible()
}

test('글을 쓰면 블로그에 쌓이고 트리에는 안 뜬다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePost(page, '9월 릴리스 소식', '검색이 훨씬 빨라졌습니다.')

  await page.getByRole('link', { name: /^blog$/i }).click()
  await expect(page.getByRole('link', { name: '9월 릴리스 소식' })).toBeVisible()
  // 목록에는 앞부분이 함께 보인다. 제목만 늘어놓으면 무엇인지 알 수 없다.
  await expect(page.getByText('검색이 훨씬 빨라졌습니다.')).toBeVisible()

  // 트리는 문서만 담는다.
  await page.goto(`/wiki/${key}`)
  await expect(page.getByText(/no pages yet/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('빈 블로그는 그렇게 말한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await page.getByRole('link', { name: /^blog$/i }).click()
  await expect(page.getByText(/news and announcements live here/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('같은 이름의 문서와 글이 함께 있을 수 있다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  // 주소가 다르다(`blog/runbook` 과 `runbook`). 이름 하나 때문에 못 쓰게
  // 만들 이유가 없다.
  await writePost(page, 'Runbook')
  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/blog/runbook$`))

  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Runbook')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/runbook$`))

  expect(consoleErrors).toEqual([])
})

test('글도 문서다 — 이력과 코멘트가 그대로 붙는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePost(page, '공지', '읽어 주세요.')

  // 별도 표를 만들지 않은 이유가 이것이다.
  await page.getByRole('button', { name: /^history$/i }).click()
  await expect(page.getByRole('button', { name: /^restore$/i }).first()).toBeVisible()

  await page.getByRole('button', { name: /comment on selection/i }).click()
  await page.getByLabel(/^comment$/i).fill('확인했습니다.')
  await page.getByRole('button', { name: /^post$/i }).click()
  await expect(page.getByText('확인했습니다.')).toBeVisible()

  expect(consoleErrors).toEqual([])
})
