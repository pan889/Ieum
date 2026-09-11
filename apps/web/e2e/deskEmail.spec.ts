/**
 * 메일 채널 화면 (feature-map C6).
 *
 * 서버 시험 108개가 규칙을 이미 붙잡고 있다. 브라우저로 한 번 더 보는 이유:
 *
 * - **step-up 이 걸린 화면이다.** 메일함 비밀번호를 받는 자리이므로 2FA 를
 *   통과하지 않은 세션에는 목록조차 주지 않는다.
 * - **비밀번호가 되돌아오지 않는다.** 값이 브라우저까지 오는지는 눈으로 봐야
 *   드러난다 — 서버 시험은 응답 객체만 보고, 화면이 그것을 어떻게 쓰는지는
 *   못 본다.
 * - **비워 둔 채 저장해도 수신이 안 멈춘다.** 폼이 빈 문자열을 보내면 서버의
 *   "안 보내면 그대로 둔다" 규칙이 무너지는데, 그건 화면 쪽 결함이다.
 * - **마지막 폴링의 오류가 보인다.** 안 보이면 조용히 아무 메일도 안 들어온다.
 */

import type { Page } from '@playwright/test'

import { expect, signIn, signInWithMfa, test, uniqueSlug } from './fixtures'

test.slow()

/** 메일 채널 화면. **2FA 를 통과한 관리자로** 들어간다. */
async function openChannels(page: Page): Promise<void> {
  await signInWithMfa(page)
  await page.goto('/settings/email-channels')
  await expect(page.getByRole('heading', { name: /email channels/i, level: 1 })).toBeVisible()
}

async function fillChannel(page: Page, address: string): Promise<void> {
  await page.getByRole('button', { name: /new channel/i }).click()
  await page.getByLabel(/^receiving address$/i).fill(address)
  await page.getByLabel(/^sending address$/i).fill(address)
  await page.getByLabel(/^host$/i).fill('imap.example')
  await page.getByLabel(/^user$/i).fill('help')
  await page.getByLabel(/^password$/i).fill('mailbox-secret-1234')
}

test.describe('메일 채널', () => {
  test('포털을 고르면 그 안의 요청 유형을 고를 수 있고, 저장하면 목록에 뜬다', async ({
    page,
  }) => {
    const { createPortal } = await import('./fixtures')
    const portal = await createPortal(page)

    await openChannels(page)
    // 채널은 프로젝트 단위다 — 포털을 만든 프로젝트로 옮긴다.
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()

    const address = `${uniqueSlug('help-')}@ours.example`
    await fillChannel(page, address)

    // **포털을 먼저 고른다.** 같은 이름의 폼이 여러 포털에 있을 수 있다.
    // 픽스처가 포털 이름을 안 내주므로 순번으로 고른다(0 은 안내 항목).
    await page.getByLabel(/^portal$/i).selectOption({ index: 1 })
    await page.getByLabel(/^request type$/i).selectOption({ index: 1 })
    await page.getByRole('button', { name: /^save$/i }).click()

    const row = page.locator('li').filter({ hasText: address })
    await expect(row).toBeVisible()
    // 아직 한 번도 안 훑었다는 것을 말한다 — 빈 칸으로 두면 "되고 있나" 를
    // 알 수 없다.
    await expect(row.getByText(/never fetched/i)).toBeVisible()
  })

  test('요청 유형을 안 고르면 저장을 막는다', async ({ page }) => {
    // 서버도 거절한다. 여기서 막으면 여섯 칸 중 어디가 문제인지 그 자리에서
    // 보인다.
    await openChannels(page)
    await fillChannel(page, `${uniqueSlug('blocked-')}@ours.example`)
    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()
  })

  test('비밀번호는 되돌아오지 않고, 비워 둔 채 고쳐도 유지된다', async ({ page }) => {
    const { createPortal } = await import('./fixtures')
    const portal = await createPortal(page)

    await openChannels(page)
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()

    const address = `${uniqueSlug('keep-')}@ours.example`
    await fillChannel(page, address)
    // 픽스처가 포털 이름을 안 내주므로 순번으로 고른다(0 은 안내 항목).
    await page.getByLabel(/^portal$/i).selectOption({ index: 1 })
    await page.getByLabel(/^request type$/i).selectOption({ index: 1 })
    await page.getByRole('button', { name: /^save$/i }).click()

    const row = page.locator('li').filter({ hasText: address })
    await expect(row).toBeVisible()
    // 비밀번호가 없으면 그렇다고 말한다 — 지금은 있으므로 안 보여야 한다.
    await expect(row.getByText(/no password/i)).toHaveCount(0)

    await row.getByRole('button', { name: /^edit$/i }).click()
    // **값이 안 온다.** 되돌려주면 그것이 브라우저의 메모리·로그·오류 보고를
    // 거쳐 다니게 된다.
    await expect(page.getByLabel(/^password$/i)).toHaveValue('')
    // 그리고 비워 둔 채 저장할 수 있다.
    await expect(page.getByRole('button', { name: /^save$/i })).toBeEnabled()
    await page.getByLabel(/^user$/i).fill('help2')
    await page.getByRole('button', { name: /^save$/i }).click()

    // 비밀번호가 지워지지 않았다.
    await expect(
      page.locator('li').filter({ hasText: address }).getByText(/no password/i),
    ).toHaveCount(0)
  })
})

test.describe('step-up', () => {
  test('2FA 를 통과하지 않으면 목록조차 주지 않는다', async ({ page }) => {
    // 메일함 비밀번호를 받는 자리다. 권한은 있고 증명이 없는 것이므로 화면이
    // 그 차이를 보여 줘야 한다.
    await signIn(page)
    await page.goto('/settings/email-channels')
    await expect(page.getByRole('heading', { name: /email channels/i, level: 1 })).toBeVisible()
    await expect(page.getByRole('alert').first()).toBeVisible()
  })
})
