/**
 * 구독과 알림함.
 *
 * 남에게 알림이 실제로 가는지는 서버 테스트가 본다(수신자가 둘 필요하다).
 * 여기서는 브라우저 없이 못 보는 것만 본다 — 구독이 새로고침을 넘어 남는가,
 * 그리고 **자기 행동이 자기에게 오지 않는가**.
 */

import { createSpace, expect, signIn, test, uniqueKey, writeBody } from './fixtures'

function spaceKey(): string {
  return uniqueKey('B')
}

/** 아웃박스는 15초마다 훑는다. 알림이 오는지 보려면 그보다 더 기다려야 한다. */
const SWEEP_MS = 20_000

async function newPage(
  page: import('@playwright/test').Page,
  title: string,
  body: string,
): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.locator('.ieum-markdown').first()).toBeVisible()
}

test('스페이스 구독은 새로고침해도 남는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  const watch = page.getByRole('button', { name: /^watch$/i })
  await expect(watch).toBeVisible()
  await watch.click()
  // 눌린 상태를 낙관적으로 그리지 않는다 — 서버가 그렇다고 해야 그렇다.
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.getByRole('button', { name: /^watching$/i }).click()
  await expect(page.getByRole('button', { name: /^watch$/i })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('문서도 따로 구독한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await newPage(page, 'Runbook', '배포 절차를 적는다.')

  // 스페이스 구독 버튼과 문서 구독 버튼이 둘 다 있다. 문서 쪽은 본문 옆이다.
  const onPage = page.getByRole('article').getByRole('button', { name: /^watch$/i })
  await onPage.click()
  await expect(
    page.getByRole('article').getByRole('button', { name: /^watching$/i }),
  ).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('내가 한 일은 나에게 알리지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^watch$/i }).click()
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await newPage(page, 'Runbook', '배포 절차를 적는다.')

  // 구독해 두었으니 알림이 만들어질 자리다. 그런데 쓴 사람은 나다.
  await page.waitForTimeout(SWEEP_MS)
  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: /notifications/i })).toBeVisible()
  await expect(page.getByText(new RegExp(key))).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('메일 받는 방식은 고른 대로 남는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/notifications')

  // 켬/끔 하나로 두면 알림마다 한 통씩 오고 사람들은 통째로 끈다. 끈
  // 사람에게는 아무것도 못 알린다 — 그래서 가운데 칸이 있다.
  const mode = page.getByLabel(/^email$/i)
  await expect(mode).toHaveValue('instant')
  await mode.selectOption('daily')
  await expect(page.getByText(/^saved\.$/i)).toBeVisible()

  await page.reload()
  await expect(page.getByLabel(/^email$/i)).toHaveValue('daily')

  // 시드 DB 를 공유하므로 되돌려 놓는다.
  await page.getByLabel(/^email$/i).selectOption('instant')
  await expect(page.getByText(/^saved\.$/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
