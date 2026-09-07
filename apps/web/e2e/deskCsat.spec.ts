/**
 * 만족도 조사 (feature-map C11).
 *
 * 서버 시험이 토큰·한 번만·값 검사를 이미 붙잡고 있다. 브라우저로 한 번 더
 * 보는 이유:
 *
 * - **로그인 앞에 있다.** 이 화면이 인증 게이트 뒤로 밀리면 게스트는 열 수
 *   없고, 그러면 기능이 아예 없는 것과 같다. 그건 라우팅의 성질이라 서버
 *   시험이 못 본다.
 * - **메일 → 링크 → 점수 → 상담원 화면** 이 이어지는지는 여기서만 본다.
 *   중간의 어느 한 칸이 빠져도 서버 시험은 전부 통과한다.
 * - **두 번째 방문에는 폼이 없다.** 답을 덮어쓸 자리를 화면이 주지 않는
 *   것까지가 "한 번만" 이다.
 */

import type { Page } from '@playwright/test'

import {
  MAILPIT,
  createPortal,
  expect,
  inviteUser,
  signIn,
  signInAsCustomer,
  test,
} from './fixtures'

test.slow()

/** 메일함에서 조사 링크를 꺼낸다. 워커가 15초마다 훑으므로 기다린다. */
async function surveyLink(page: Page, email: string): Promise<string> {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    const found = await page.request.get(
      `${MAILPIT}/api/v1/search?query=${encodeURIComponent(email)}`,
    )
    const messages = ((await found.json()) as { messages?: { ID: string }[] }).messages ?? []
    for (const message of messages) {
      const detail = await page.request.get(`${MAILPIT}/api/v1/message/${message.ID}`)
      const body = ((await detail.json()) as { Text?: string }).Text ?? ''
      const link = /https?:\/\/\S+\/survey\?token=\S+/.exec(body)?.[0]
      if (link) return link
    }
    await page.waitForTimeout(3000)
  }
  throw new Error(`조사 메일이 오지 않았다: ${email}`)
}

test.describe('만족도 조사', () => {
  test('닫힌 티켓의 링크로 로그인 없이 점수를 남기면 상담원이 본다', async ({ page }) => {
    const portal = await createPortal(page)
    const customer = await inviteUser(page, { isCustomer: true })

    // 고객이 요청을 낸다.
    const context = await page.context().browser()?.newContext()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('만족도 확인용')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('만족도 확인용')
    const shown = await customerPage.locator('body').innerText()
    const issueKey = (/\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown) as RegExpExecArray)[1] as string
    await (context as NonNullable<typeof context>).close()

    // 상담원이 티켓을 닫는다. 기본 워크플로우에서 `done` 분류에 닿는 길은
    // Open → In Progress → Resolved 다.
    await signIn(page)
    await page.goto(`/issues/${issueKey}`)
    await page.getByRole('button', { name: /^start progress$/i }).click()
    await page.getByRole('button', { name: /^resolve$/i }).click()
    await expect(page.getByRole('button', { name: /^close$/i })).toBeVisible()

    const link = await surveyLink(page, customer.email)

    // **로그인하지 않은 창에서 연다.** 이게 이 스펙의 요점이다.
    const guest = await page.context().browser()?.newContext()
    const guestPage = await (guest as NonNullable<typeof guest>).newPage()
    const path = new URL(link).pathname + new URL(link).search
    await guestPage.goto(path)
    await expect(guestPage.getByRole('heading', { name: /rate this request/i })).toBeVisible()
    await expect(guestPage.getByText('만족도 확인용')).toBeVisible()

    // 점수를 안 고르면 못 보낸다 — 한마디만 오는 답을 받으면 평균의 분모를
    // 말할 수 없다.
    await expect(guestPage.getByRole('button', { name: /^send$/i })).toBeDisabled()
    await guestPage.getByRole('button', { name: /^very good$/i }).click()
    await guestPage.getByLabel(/anything to add/i).fill('빨랐습니다')
    await guestPage.getByRole('button', { name: /^send$/i }).click()
    await expect(guestPage.getByText(/thank you/i)).toBeVisible()

    // 두 번째 방문에는 폼이 없다. 답을 덮어쓸 자리를 주지 않는 것까지가
    // "한 번만" 이다.
    await guestPage.goto(path)
    await expect(guestPage.getByText(/thank you/i)).toBeVisible()
    await expect(guestPage.getByRole('button', { name: /^send$/i })).toHaveCount(0)
    await (guest as NonNullable<typeof guest>).close()

    // 상담원 화면에 점수와 한마디가 온다. 안 보이면 모은 의미가 없다.
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByText(/^satisfaction$/i)).toBeVisible()
    await expect(page.getByText('빨랐습니다')).toBeVisible()
  })

  test('망가진 링크는 그렇다고 말한다', async ({ page }) => {
    // 만료·위조·지워진 티켓을 하나로 말한다 — 어떤 링크가 살아 있는지
    // 알려 주는 자리가 아니다.
    await page.goto('/survey?token=not-a-real-token')
    await expect(page.getByText(/no longer works/i)).toBeVisible()
    await page.goto('/survey')
    await expect(page.getByText(/no longer works/i)).toBeVisible()
  })
})
