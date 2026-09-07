/**
 * 고객 포털 (feature-map C1).
 *
 * 브라우저에서만 드러나는 것들을 여기서 붙잡는다:
 *
 * - **게스트 요청이 로그인 화면에 가로채이지 않는가.** 포털을 인증 게이트
 *   뒤에 두면 이 기능은 존재하지 않는다. 서버 시험으로는 절대 안 잡힌다.
 * - **고객이 내부 앱 셸을 보지 않는가.** 셸을 재사용하면 메뉴는 다 보이는데
 *   누르는 곳마다 403 이다.
 * - **초대받은 고객이 어디로 도착하는가.** 기억해 둔 창구가 없는 새 브라우저
 *   에서 앱 뿌리로 들어오면, 예전에는 "포털을 쓰세요" 라고만 적힌 막다른
 *   화면이었다.
 * - **관리자가 입력한 라벨이 번역되지 않는가.** 서버는 화면의 언어를 모른다.
 */

import type { BrowserContext, Page } from '@playwright/test'

import { createPortal, expect, inviteUser, signIn, signInAsCustomer, test } from './fixtures'

/**
 * 깨끗한 브라우저 컨텍스트. **관리자 세션을 물고 가면 안 된다** — 게스트
 * 길을 보는 게 아니게 되고, 고객 길에서는 앞사람의 토큰으로 요청이 나간다.
 */
async function freshContext(page: Page): Promise<BrowserContext> {
  const browser = page.context().browser()
  expect(browser, '브라우저 핸들이 없다').toBeTruthy()
  return (browser as NonNullable<typeof browser>).newContext()
}

/** 메일을 실제로 읽어 초대를 수락하므로 아웃박스 훑기를 기다린다. */
test.slow()

test.describe('게스트 요청', () => {
  test('로그인 없이 폼을 받아 요청을 낸다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: true })

    // **새 컨텍스트**다. 관리자 세션을 물고 가면 게스트 길을 보는 게 아니다.
    const guest = await freshContext(page)
    const anon = await guest.newPage()
    await anon.goto(`/portal/${portal.slug}`)

    // 로그인 화면이 가로채지 않았는가 — 이게 이 스펙의 첫 단언이다.
    await expect(anon.getByRole('heading', { level: 1 })).toContainText(
      /what do you need help with|무엇을 도와/i,
    )
    // 내부 네비게이션이 없다.
    await expect(anon.getByRole('link', { name: /^projects$/i })).toHaveCount(0)

    await anon.getByRole('button', { name: /broken thing/i }).click()
    // 관리자가 입력한 라벨이 그대로 뜬다(번역 대상이 아니다). `getByLabel`
    // 로 찾는다 — `getByText` 는 "Still needed: <라벨>" 안내에도 걸려
    // strict 모드에서 둘을 함께 집는다.
    await expect(anon.getByLabel(new RegExp(portal.formLabel))).toBeVisible()

    await anon.getByLabel(new RegExp(portal.formLabel)).fill('프린터가 안 됩니다')
    await anon.getByLabel(/your name/i).fill('학부모')
    await anon.getByLabel(/your email/i).fill('parent@school.example.com')
    await anon.getByRole('button', { name: /send request/i }).click()

    // 접수 확인에 **요청 번호**가 있다. 게스트는 자기 티켓을 열 세션이 없으니
    // 이 번호가 유일한 손잡이다.
    await expect(anon.getByText(/we got your request/i)).toBeVisible()
    await expect(anon.getByText(/request number/i)).toBeVisible()
    await guest.close()
  })

  test('필수 항목이 남았으면 무엇이 남았는지 말한다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: true })

    const guest = await freshContext(page)
    const anon = await guest.newPage()
    await anon.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)

    // 라벨에 `*` 가 있다. `required` 는 입력 요소에만 가므로 눈으로 보는
    // 사람에게는 이 표시가 유일한 단서다. 정확히 일치로 찾는다 — 부분
    // 일치는 "Still needed" 안내까지 집는다.
    await expect(anon.getByText(`${portal.formLabel} *`, { exact: true })).toBeVisible()
    // 그리고 비활성 버튼이 이유를 말한다 — 말없이 눌리지 않는 버튼은
    // "손잡이는 있고 대상만 없는" 그 자리다.
    await expect(anon.getByText(/still needed/i)).toContainText(portal.formLabel)
    await expect(anon.getByRole('button', { name: /send request/i })).toBeDisabled()
    await guest.close()
  })

  test('로그인해야 하는 창구는 게스트를 거절한다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: false })

    const guest = await freshContext(page)
    const anon = await guest.newPage()
    await anon.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await anon.getByLabel(new RegExp(portal.formLabel)).fill('막힌 요청')
    await anon.getByLabel(/your name/i).fill('누구')
    await anon.getByLabel(/your email/i).fill('who@example.com')
    await anon.getByRole('button', { name: /send request/i }).click()
    await expect(anon.getByRole('alert')).toContainText(/sign in/i)
    await guest.close()
  })
})

test.describe('고객 계정', () => {
  test('초대받은 고객이 자기 창구로 도착한다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)
    const customer = await inviteUser(page, { isCustomer: true })

    // **새 컨텍스트**다. 포털을 한 번도 열지 않은 브라우저에서 앱 뿌리로
    // 들어오는 것이 이 스펙의 요점이다 — 기억해 둔 창구가 없다.
    const theirs = await freshContext(page)
    const customerPage = await theirs.newPage()
    await signInAsCustomer(customerPage, customer)

    // **주소가 내부 화면에 남지 않는다.** 예전에는 `me` 가 도착하기 전에
    // 라우터가 그려져 `/` → `/projects` 로 튀고, 내용만 이 화면으로
    // 바뀌었다 — 고객의 주소창에 자기와 무관한 주소가 적혀 있었다.
    expect(new URL(customerPage.url()).pathname).not.toBe('/projects')

    // 방금 만든 창구가 고를 수 있게 있다. 눌러서 도착한다.
    await customerPage.getByRole('link', { name: `Support ${portal.slug}` }).click()
    await customerPage.waitForURL(new RegExp(`/portal/${portal.slug}`))
    await expect(customerPage.getByText(customer.displayName)).toBeVisible()
    await expect(customerPage.getByRole('link', { name: /^projects$/i })).toHaveCount(0)
    await theirs.close()
  })

  test('낸 요청이 내 목록에 남는다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)
    const customer = await inviteUser(page, { isCustomer: true })

    const theirs = await freshContext(page)
    const customerPage = await theirs.newPage()
    await signInAsCustomer(customerPage, customer)

    await customerPage.goto(`/portal/${portal.slug}`)
    // 처음에는 비어 있다. **오류가 아니라 빈 목록**이라고 말해야 한다.
    await expect(customerPage.getByText(/have not made any requests/i)).toBeVisible()

    await customerPage.getByRole('button', { name: /broken thing/i }).click()
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('내가 낸 요청')
    await customerPage.getByRole('button', { name: /send request/i }).click()

    // 로그인한 고객은 상세로 간다 — 진행을 볼 수 있으므로.
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('내가 낸 요청')

    await customerPage.goto(`/portal/${portal.slug}`)
    await expect(customerPage.getByText('내가 낸 요청')).toBeVisible()
    await theirs.close()
  })

  test('남의 요청은 보이지 않는다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)
    const mine = await inviteUser(page, { isCustomer: true })
    const theirs = await inviteUser(page, { isCustomer: true })

    // 첫 고객이 요청을 낸다.
    const first = await freshContext(page)
    const firstPage = await first.newPage()
    await signInAsCustomer(firstPage, mine)
    await firstPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await firstPage.getByLabel(new RegExp(portal.formLabel)).fill('첫 고객의 요청')
    await firstPage.getByRole('button', { name: /send request/i }).click()
    await expect(firstPage.getByRole('heading', { level: 1 })).toContainText('첫 고객의 요청')
    const ticketUrl = firstPage.url()
    await first.close()

    // 두 번째 고객은 목록에서도, 주소를 직접 열어도 못 본다. 조직이 없는
    // 고객이 **조직 미지정 티켓 전부**를 보게 되는 실수가 이 자리다.
    const second = await freshContext(page)
    const secondPage = await second.newPage()
    await signInAsCustomer(secondPage, theirs)
    await secondPage.goto(`/portal/${portal.slug}`)
    await expect(secondPage.getByText('첫 고객의 요청')).toHaveCount(0)

    await secondPage.goto(new URL(ticketUrl).pathname)
    await expect(secondPage.getByRole('alert')).toBeVisible()
    await expect(secondPage.getByText('첫 고객의 요청')).toHaveCount(0)
    await second.close()
  })
})

test.describe('관리 화면', () => {
  test('고객이 여는 주소를 그대로 보여 준다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)
    await page.goto('/settings/portals')

    // 포털은 프로젝트에 붙으므로 프로젝트를 골라야 목록이 뜬다.
    //
    // **드롭다운이 아니라 검색이다.** 처음에는 `<select>` 에 프로젝트 100개를
    // 채웠고, 개발 DB 의 프로젝트가 그 수를 넘자 이 스펙이 "방금 만든
    // 프로젝트가 옵션에 없다" 로 멈췄다 — 사람 피커에서 이미 한 번 겪은
    // 결함을 두 번째로 만든 자리다. 그래서 스펙도 검색으로 찾는다.
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()

    const found = page.getByRole('listitem').filter({ hasText: portal.slug })
    await expect(found).toBeVisible()
    // 관리자가 이 링크를 복사해 고객에게 준다. 손으로 옮겨 적게 하지 않는다.
    await expect(found.getByRole('link', { name: `/portal/${portal.slug}` })).toBeVisible()
  })
})
