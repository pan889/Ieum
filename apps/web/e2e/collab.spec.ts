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
 * - **붙기 전에 쓴 글이 살아남는다.** 소켓이 늦게 붙는 사이에 친 글은 공유
 *   문서가 아니라 내 상태에 쌓인다. 갈아타는 순간 그것을 공유에 싣지 않으면
 *   **사람이 쓴 글이 사라진다** — 느린 연결에서 문서를 열고 바로 쓰면 그렇게
 *   된다. 한동안 이 파일의 헬퍼가 "붙을 때까지 기다림" 으로 그 결함을 피해
 *   갔고, 그 대기가 없는 다른 스펙(wysiwyg)이 전체 스위트에서 무작위로
 *   붉어지는 것으로 그 사실이 드러났다.
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
  // 아래 시험들은 **붙은 뒤**의 주고받음을 보는 것이므로 붙을 때까지
  // 기다린다. 붙기 전에 쓴 글이 살아남는지는 따로 본다(맨 아래).
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

test('붙기 전에 쓴 글은 갈아탈 때 살아남는다', async ({ page, consoleErrors }) => {
  /**
   * **이 시험이 붙잡는 것이 사람이 글을 잃는 자리다.**
   *
   * 붙기 전에는 내 상태가 본문이고, 붙으면 공유 문서가 임자가 된다
   * (`PageDetail` 주석). 그 갈아타기에서 내 상태를 공유에 싣지 않으면,
   * 붙는 사이에 친 글이 조용히 사라진다.
   *
   * 실제 창은 소켓이 늦게 붙을 때 열린다. 여기서는 표 발급을 일부러 늦춰
   * **확정적으로** 그 창을 만든다 — 안 그러면 빠른 기계에서는 창이 너무
   * 좁아 시험이 통과해 버리고, 느린 기계에서만 붉어진다.
   */
  await page.route('**/collab-ticket', async (route) => {
    await new Promise((resolve) => { setTimeout(resolve, 2500) })
    await route.continue()
  })

  await signIn(page)
  const key = uniqueKey('L')
  await createSpace(page, key)
  await newPage(page, key, '늦게 붙는 문서')

  // **붙기 전에** 편집으로 들어가 쓴다. 기다리지 않는 것이 요점이다.
  await page.getByRole('button', { name: /^edit$/i }).click()
  const write = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await write.getAttribute('aria-selected')) !== 'true') await write.click()
  const box = page.locator('[role="tabpanel"] textarea').first()
  await expect(box).toBeVisible()
  await box.fill('붙기 전에 쓴 글')

  // 붙는다.
  await expect(page.getByTestId('collab-presence')).toContainText(/editing together/i)

  // 내가 쓴 글이 그대로 있다.
  await expect(box).toHaveValue('붙기 전에 쓴 글')
  // 그리고 그것이 **공유 문서에** 들어가 있다 — 이어서 치는 글자가 붙는다.
  await box.click()
  await page.keyboard.press('End')
  await page.keyboard.type('!')
  await expect(box).toHaveValue('붙기 전에 쓴 글!')

  expect(consoleErrors).toEqual([])
})

/**
 * 두 번째 오리진. 있으면 **다른 API 인스턴스에 붙은** 창을 띄울 수 있다.
 *
 * 없으면 아래 시험을 건너뛴다 — 인스턴스를 둘 띄우는 것은 개발 기본값이
 * 아니고, 없는데 억지로 돌리면 "같은 인스턴스에서 됐다" 를 "HA 에서 됐다" 로
 * 잘못 읽게 된다.
 */
const SECOND_ORIGIN = process.env['E2E_SECOND_ORIGIN'] ?? ''

test('다른 API 인스턴스에 붙어도 같이 편집된다', async ({ page, browser, consoleErrors }) => {
  /**
   * **HA 가이드가 "API 를 여러 개 띄워도 된다" 고 말할 근거다.**
   *
   * 방(CRDT 상태)은 프로세스 메모리에 있고, 프로세스 사이는 Redis pub/sub 로
   * 잇는다(`collab.py` 의 `_publish`). 코드는 그렇게 보이지만, 그 사슬이
   * 실제로 이어지는지는 **인스턴스를 둘 띄워 봐야** 안다 — 한 인스턴스에서
   * 도는 시험은 이 성질에 대해 아무것도 말해 주지 않는다.
   *
   * 편집과 **프레즌스를 함께** 본다. 편집만 전달되고 프레즌스가 안 넘어오면
   * 사람은 자기가 혼자라고 생각하고 쓰다가 나중에 놀란다.
   */
  test.skip(SECOND_ORIGIN === '', 'E2E_SECOND_ORIGIN 이 없다 (인스턴스 하나로 도는 중)')

  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Across Instances')
  const mine = await openEditor(page)

  // 두 번째 창은 **다른 오리진**을 본다. 그 오리진의 앱은 다른 API 를 부른다.
  const second = await browser.newContext({ baseURL: SECOND_ORIGIN })
  const other = await second.newPage()
  try {
    await signIn(other)
    await other.goto(`/wiki/${key}/across-instances`)
    const theirs = await openEditor(other)

    // **프레즌스가 프로세스를 넘는다.** awareness 도 Redis 로 나간다.
    await expect(page.getByTestId('collab-peer').first()).toBeVisible({ timeout: 15_000 })
    await expect(other.getByTestId('collab-peer').first()).toBeVisible({ timeout: 15_000 })

    // 한쪽에서 쓴 글이 다른 인스턴스의 창에 뜬다.
    await mine.click()
    await mine.pressSequentially('첫째 인스턴스에서 쓴 글')
    await expect(theirs).toHaveValue(/첫째 인스턴스에서 쓴 글/, { timeout: 20_000 })

    // 반대 방향도. 한 방향만 되면 발행은 되고 구독이 안 되는 것이다.
    await theirs.click()
    await other.keyboard.press('End')
    await other.keyboard.type(' / 둘째에서 덧붙인 글')
    await expect(mine).toHaveValue(/둘째에서 덧붙인 글/, { timeout: 20_000 })
  } finally {
    await second.close()
  }

  expect(consoleErrors).toEqual([])
})
