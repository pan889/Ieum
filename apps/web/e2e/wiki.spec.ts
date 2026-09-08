/**
 * 위키. 스페이스 → 트리 → 문서 보기·편집·이력.
 *
 * 문서 주소는 URL 이 소유한다(`/wiki/ENG/deploy/rollback`). 링크 하나로 같은
 * 문서가 열려야 하는데, 이건 라우터·splat 파싱이 얽혀 있어 브라우저 없이는
 * 확인할 수 없다.
 */

import { bodyField, createSpace, expect, signIn, test, uniqueKey, writeBody } from './fixtures'

function spaceKey(): string {
  return uniqueKey('W')
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
  await writeBody(page, '# Steps\n\n1. build\n2. deploy')
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
  await writeBody(page, 'first draft')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('first draft')).toBeVisible()

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, 'second draft')
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

test('판 사이 차이를 줄 단위로 본다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Diffed')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '| 항목 | 값 |\n| --- | --- |\n| a | 1 |')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('cell', { name: '1', exact: true })).toBeVisible()

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '| 항목 | 값 |\n| --- | --- |\n| a | 2 |')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('cell', { name: '2', exact: true })).toBeVisible()

  await page.getByRole('button', { name: /^history$/i }).click()
  await page.getByRole('button', { name: /^compare$/i }).first().click()

  // 표 한 칸만 고쳤으면 한 줄만 바뀐 것으로 보여야 한다 — 마크다운을
  // 정본으로 고른 부수 이득이다.
  await expect(page.getByText('+1', { exact: true })).toBeVisible()
  await expect(page.getByText('−1', { exact: true })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('저장하지 않고 떠나도 편집이 남는다', async ({ page, consoleErrors }) => {
  /**
   * 지키는 약속은 **"저장 안 한 편집이 남는다"** 다. 그것을 들고 있는 것이
   * 개인 초안(`page_draft`)이 아니라 공유 문서(`page_collab`)로 바뀌었다
   * (B16) — 같이 편집하는 자리에서는 "내 초안" 이라는 것이 없으므로.
   *
   * 그래서 여기서 보는 것은 기계가 아니라 약속이다: 친 글이 남아 있는가,
   * 화면이 그렇다고 말하는가, 게시한 뒤에는 그 말이 사라지는가.
   */
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Long doc')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '한참 쓰다 만 글이다.')
  await expect(page.getByText(/draft saved/i)).toBeVisible()

  // 창을 닫았다 다시 연 셈이다.
  await page.reload()
  await page.getByRole('button', { name: /^edit$/i }).click()
  // **아직 아무도 게시하지 않은 편집이 있다고 말한다.** 안 말하면 사람은
  // 지금 보는 글이 게시된 본문인 줄 안다.
  await expect(page.getByText(/nobody has published/i)).toBeVisible()
  await expect(await bodyField(page)).toHaveValue('한참 쓰다 만 글이다.')

  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('한참 쓰다 만 글이다.')).toBeVisible()

  // 게시했으면 "안 게시한 편집" 은 없다. 남기면 다음에 열 때 거짓말이 된다.
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(page.getByText(/nobody has published/i)).toHaveCount(0)
  await expect(page.getByText(/restored your unsaved edit/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('초안을 버리면 저장된 본문으로 돌아간다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Discardable')
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '저장된 본문')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('저장된 본문')).toBeVisible()

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '버릴 편집')
  await expect(page.getByText(/draft saved/i)).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(await bodyField(page)).toHaveValue('버릴 편집')
  await page.getByRole('button', { name: /discard it/i }).click()

  // **버리기는 공유 문서까지 되돌린다.** 개인 초안만 지우면 화면은 그대로
  // 공유 문서를 보여 주므로 눌러도 아무 일이 없는 것처럼 보인다.
  await expect(await bodyField(page)).toHaveValue('저장된 본문')
  await expect(page.getByText(/nobody has published/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('문서를 가지째 복사한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await createPage(page, 'Original')
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '원본 본문이다.')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('원본 본문이다.')).toBeVisible()

  await page.getByRole('link', { name: 'Original', exact: true }).first().hover()
  await page.getByRole('button', { name: /new page under original/i }).click()
  await page.getByLabel(/^title$/i).fill('Child')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: 'Child' })).toBeVisible()

  await page.goto(`/wiki/${key}/original`)
  await page.getByRole('button', { name: /^duplicate$/i }).click()

  const copy = page.getByRole('link', { name: /Original \(copy\)/ })
  await expect(copy).toBeVisible()
  await copy.click()
  // 본문과 하위 문서가 함께 따라온다.
  await expect(page.getByText('원본 본문이다.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Child' })).toHaveCount(2)

  expect(consoleErrors).toEqual([])
})
