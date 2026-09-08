/**
 * 동시 편집 — 두 창을 띄워 실제로 같이 쓴다 (feature-map B16).
 *
 * 서버 시험 33개가 방과 문을 붙잡고, 프론트 단위 18개가 캐럿 계산을 붙잡는다.
 * 브라우저로 한 번 더 보는 이유는 **배선 전체가 한 번은 실제로 이어져야**
 * 하기 때문이다: 표를 받고, 소켓을 열고, 프로토콜을 주고받고, 두 화면이 같은
 * 글자를 보는 것 — 그 사슬 중 하나만 끊겨도 서버 시험은 다 통과하는데 화면은
 * 조용히 혼자 편집한다.
 *
 * 붙잡는 것:
 *
 * - **한쪽이 친 글자가 다른 쪽에 뜬다.** 이게 안 되면 기능이 없는 것이다.
 * - **둘이 같은 순간에 쳐도 둘 다 남는다.** 낙관적 잠금이라면 한쪽이 거절
 *   당하는데, CRDT 는 둘을 보존한다. 그 차이가 이 기능의 이유다.
 * - **누가 같이 보고 있는지 화면이 말한다.** 안 보이면 사람은 자기가 혼자라고
 *   생각하고 편집하고, 그 다음에 놀란다.
 * - **혼자일 때도 붙었는지 말한다.** 끊긴 채로 계속 타이핑하다 나중에 "내가
 *   쓴 게 안 갔다" 를 겪으면 그때는 이미 늦었다.
 */

import type { Locator, Page } from '@playwright/test'

import { createSpace, expect, signIn, test, uniqueKey } from './fixtures'

/** 위키 스페이스 키. `wiki.spec.ts` 와 같은 모양이다. */
function spaceKey(): string {
  return uniqueKey('S')
}

test.slow()

async function newPage(page: Page, key: string, title: string): Promise<void> {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

/** 편집 모드로 들어가 소스 탭의 textarea 를 잡는다. */
async function openEditor(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: /^edit$/i }).click()
  const write = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await write.getAttribute('aria-selected')) !== 'true') await write.click()
  const box = page.locator('[role="tabpanel"] textarea').first()
  await expect(box).toBeVisible()
  // 붙기 전에 치면 그 글자는 공유 문서에 안 들어간다.
  await expect(page.getByTestId('collab-presence')).toContainText(/editing together/i)
  return box
}

test('혼자 열어도 붙었다고 말한다', async ({ page, consoleErrors }) => {
  // **끊긴 채로 타이핑하게 두지 않는다.** 나중에 "안 갔다" 를 겪으면 늦다.
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Alone Doc')
  await openEditor(page)

  await expect(page.getByTestId('collab-presence')).toContainText(/only one here/i)
  await expect(page.getByTestId('collab-peer')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('한쪽이 친 글자가 다른 쪽에 뜬다', async ({ page, browser, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Together Doc')
  const mine = await openEditor(page)

  // 두 번째 창. 같은 사람이어도 된다 — 서버는 연결마다 방에 넣는다.
  const second = await browser.newContext()
  const other = await second.newPage()
  try {
    await signIn(other)
    await other.goto(`/wiki/${key}/together-doc`)
    const theirs = await openEditor(other)

    // 서로가 보인다.
    await expect(page.getByTestId('collab-peer').first()).toBeVisible()
    await expect(other.getByTestId('collab-peer').first()).toBeVisible()

    await mine.click()
    await mine.pressSequentially('왼쪽에서 쓴 글')

    // **다른 창에 뜬다.** 이게 이 기능이다.
    await expect(theirs).toHaveValue(/왼쪽에서 쓴 글/, { timeout: 15_000 })
  } finally {
    await second.close()
  }

  expect(consoleErrors).toEqual([])
})

test('둘이 같이 쳐도 둘 다 남는다', async ({ page, browser, consoleErrors }) => {
  /**
   * **낙관적 잠금과 갈라지는 자리다.** `If-Match` 라면 나중에 저장한 쪽이
   * 409 로 거절당한다 — 맞는 동작이지만 같이 쓰는 자리에서는 답이 아니다.
   * 텍스트 CRDT 는 둘을 보존하고, 그 차이가 이 기능의 이유다.
   */
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Both Doc')
  const mine = await openEditor(page)

  const second = await browser.newContext()
  const other = await second.newPage()
  try {
    await signIn(other)
    await other.goto(`/wiki/${key}/both-doc`)
    const theirs = await openEditor(other)
    await expect(page.getByTestId('collab-peer').first()).toBeVisible()

    // 서로 다른 자리에 쓴다. 한쪽은 앞, 한쪽은 뒤.
    await mine.click()
    await mine.pressSequentially('AAA')
    await expect(theirs).toHaveValue(/AAA/, { timeout: 15_000 })

    await theirs.click()
    await theirs.press('End')
    await theirs.pressSequentially('BBB')

    // 양쪽 화면이 둘 다 갖는다.
    await expect(mine).toHaveValue(/AAA/, { timeout: 15_000 })
    await expect(mine).toHaveValue(/BBB/, { timeout: 15_000 })
    await expect(theirs).toHaveValue(/AAA/)
    await expect(theirs).toHaveValue(/BBB/)
  } finally {
    await second.close()
  }

  expect(consoleErrors).toEqual([])
})

test('게시하면 두 사람의 편집이 한 판에 담긴다', async ({ page, browser, consoleErrors }) => {
  /**
   * 공유 초안은 판을 만들지 않는다 — **게시는 사람이 한다.** 그때 담기는
   * 본문이 두 사람의 편집을 다 갖고 있어야 한다. 안 그러면 CRDT 는 맞는데
   * 저장되는 것이 틀린 셈이다.
   */
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Publish Doc')
  const mine = await openEditor(page)

  const second = await browser.newContext()
  const other = await second.newPage()
  try {
    await signIn(other)
    await other.goto(`/wiki/${key}/publish-doc`)
    const theirs = await openEditor(other)
    await expect(page.getByTestId('collab-peer').first()).toBeVisible()

    await mine.click()
    await mine.pressSequentially('# 함께')
    await expect(theirs).toHaveValue(/# 함께/, { timeout: 15_000 })
    await theirs.press('End')
    await theirs.pressSequentially(' 쓴 문서')
    await expect(mine).toHaveValue(/# 함께 쓴 문서/, { timeout: 15_000 })

    await page.getByRole('button', { name: /^save$/i }).click()
    // 게시된 판에 둘의 글자가 다 있다.
    await expect(page.getByRole('heading', { name: '함께 쓴 문서' })).toBeVisible()
  } finally {
    await second.close()
  }

  expect(consoleErrors).toEqual([])
})
