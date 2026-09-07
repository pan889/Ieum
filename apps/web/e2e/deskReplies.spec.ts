/**
 * 내부 노트 vs 고객 회신 (feature-map C7).
 *
 * **고객 화면에 내부 노트가 한 번이라도 뜨면 그걸로 끝이다.** 서버 시험이
 * 그것을 이미 붙잡고 있지만(`test_desk_replies.py`), 여기서 한 번 더 보는
 * 이유가 있다: 화면이 다른 API 로 코멘트를 받아 그릴 수도 있고, 그러면
 * 서버 계약은 멀쩡한데 고객 화면에 내부 노트가 뜬다. **눈으로 보는 것이
 * 마지막 확인이다.**
 *
 * 그리고 상담원 쪽의 판단을 함께 고정한다: 티켓에는 체크박스가 아니라
 * **버튼 둘**이 있다. 체크박스는 한 번의 미스클릭으로 두 방향 모두 사고가
 * 된다 — 꺼진 채로 쓰면 내부 노트가 고객에게 가고, 켜진 채로 쓰면 회신이
 * 고객에게 닿지 않는다.
 */

import type { BrowserContext, Page } from '@playwright/test'

import { createPortal, expect, inviteUser, signIn, signInAsCustomer, test } from './fixtures'

/** 초대 메일을 실제로 읽는다. */
test.slow()

async function freshContext(page: Page): Promise<BrowserContext> {
  const browser = page.context().browser()
  expect(browser, '브라우저 핸들이 없다').toBeTruthy()
  return (browser as NonNullable<typeof browser>).newContext()
}

/** 고객 하나가 요청을 하나 낸다. 상담원 쪽에서 쓸 이슈 키를 돌려준다. */
async function fileRequest(
  page: Page,
  summary: string,
): Promise<{ slug: string; issueKey: string; customerPage: Page; context: BrowserContext }> {
  const portal = await createPortal(page)
  const customer = await inviteUser(page, { isCustomer: true })

  const context = await freshContext(page)
  const customerPage = await context.newPage()
  await signInAsCustomer(customerPage, customer)
  await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
  await customerPage.getByLabel(new RegExp(portal.formLabel)).fill(summary)
  await customerPage.getByRole('button', { name: /send request/i }).click()
  await expect(customerPage.getByRole('heading', { level: 1 })).toContainText(summary)

  // 화면에 뜬 티켓 키를 그대로 쓴다. 목록을 훑으면 페이지에 없을 수 있다.
  const shown = await customerPage.locator('body').innerText()
  const found = /\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown)
  expect(found, '티켓 키를 화면에서 찾지 못했다').toBeTruthy()

  // **관리자로 다시 들어간다.** `inviteUser` 가 이 페이지를 `/invite` 로
  // 보내 활성화까지 하고 나면 관리자 세션이 남아 있지 않다 — 그 화면은
  // 로그인 전에 여는 자리이기 때문이다. 다른 관리 스펙들도 초대 뒤에
  // 다시 로그인한다(`adminConsole.spec.ts`).
  await signIn(page)

  return {
    slug: portal.slug,
    issueKey: (found as RegExpExecArray)[1] as string,
    customerPage,
    context,
  }
}

/**
 * 코멘트 편집기에 글을 넣는다. **`fill` 을 쓰지 않는다.**
 *
 * 기본 편집기는 서식 모드(contenteditable)다. 갓 그려진 편집기에는 `fill` 이
 * 통하고, 그래서 `issues.spec.ts` 는 그렇게 쓴다 — 거기서는 **한 번만**
 * 넣는다.
 *
 * 이 파일은 같은 편집기에 연달아 두 번 넣는 유일한 자리다. 제출이 성공하면
 * 화면이 값을 `''` 로 되돌리고 편집기는 `setContent` 로 비워지는데, 그
 * 처리가 끝나기 전에 `fill` 이 DOM 을 건드리면 편집기가 다음 정리에서
 * 그 변경을 버린다. 그러면 `onChange` 가 오지 않아 상태는 계속 빈 문자열이고,
 * 버튼은 끝까지 비활성인 채로 90초를 기다리다 죽는다. 실제로 그랬다.
 *
 * 사람이 쓰는 길로 넣는다: 눌러서 초점을 주고, 실제 키 입력으로 적는다.
 * 그리고 **버튼이 살아나는 것까지 본다** — 값이 들어갔다는 유일한 증거다.
 */
async function compose(page: Page, text: string, submit: RegExp): Promise<void> {
  const editor = page.getByLabel(/^comments$/i)
  await editor.click()
  await editor.pressSequentially(text)
  await expect(page.getByRole('button', { name: submit })).toBeEnabled()
}

test.describe('내부 노트', () => {
  test('고객에게는 공개 회신만 보인다', async ({ page }) => {
    // **제목에 노트의 낱말을 쓰지 않는다.** 처음에는 요청 제목이
    // `내부 노트 확인용` 이었고, 아래의 "없어야 한다" 가 제목에 걸려
    // 붉었다 — 제목은 고객이 직접 쓴 글이라 당연히 보인다. 새는 것을
    // 잡아야 하는 시험이 **안 새는 것을 잡고** 실패하면, 다음 사람은
    // 시험을 느슨하게 고쳐서 통과시킨다. 그래서 노트 본문에만 있는
    // 표식으로 본다.
    const filed = await fileRequest(page, '회신 확인용')
    const note = '이 문장은 고객에게 절대 나가지 않는다'

    // 상담원(관리자)으로 티켓을 열어 둘을 각각 쓴다.
    await page.goto(`/issues/${filed.issueKey}`)

    await compose(page, note, /add internal note/i)
    await page.getByRole('button', { name: /add internal note/i }).click()
    await expect(page.getByText(note)).toBeVisible()

    await compose(page, '고객에게 보이는 회신입니다', /reply to customer/i)
    await page.getByRole('button', { name: /reply to customer/i }).click()
    await expect(page.getByText('고객에게 보이는 회신입니다')).toBeVisible()

    // 상담원은 둘 다 본다 — 조이다가 상담원까지 막으면 고친 것이 아니다.
    await expect(page.getByText(/^internal$/i).first()).toBeVisible()

    // **고객 화면.** 이 세 줄이 이 파일의 이유다. 공개 회신이 보이는 것을
    // 먼저 확인해야 한다 — 대화가 아예 안 그려졌는데 "노트도 없다" 로
    // 통과하는 시험은 아무 것도 지키지 않는다.
    await filed.customerPage.reload()
    await expect(filed.customerPage.getByText('고객에게 보이는 회신입니다')).toBeVisible()
    await expect(filed.customerPage.getByText(note)).toHaveCount(0)
    await filed.context.close()
  })

  test('티켓에는 체크박스가 아니라 버튼 둘이 있다', async ({ page }) => {
    const filed = await fileRequest(page, '버튼 확인용')
    await page.goto(`/issues/${filed.issueKey}`)

    await expect(page.getByRole('button', { name: /reply to customer/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /add internal note/i })).toBeVisible()
    // 체크박스가 남아 있으면 미스클릭의 자리가 남는다.
    await expect(page.getByLabel(/^internal note$/i)).toHaveCount(0)
    // 무엇이 고객에게 보이는지 화면이 말한다.
    await expect(page.getByText(/a reply is visible to the customer/i)).toBeVisible()
    await filed.context.close()
  })

  test('티켓이 아닌 이슈는 체크박스를 그대로 쓰고 콘솔이 조용하다', async ({
    page,
    consoleErrors,
  }) => {
    // 잘 돌던 화면을 바꾸지 않는다는 판단을 고정한다. 티켓이 아닌 이슈에는
    // 고객이 없으므로 미스클릭의 위험이 없다.
    const { createIssue, createProject, projectKey } = await import('./fixtures')
    const key = projectKey()
    await signIn(page)
    await createProject(page, key)

    // **응답을 기다린다.** 이 화면은 "이 이슈가 티켓인가" 를 서버에 묻고,
    // 처음에는 서버가 404 로 답했다 — 그래서 평범한 이슈를 열 때마다 콘솔에
    // 오류가 하나 찍혔다. 이슈 상세는 이 제품에서 가장 많이 열리는 화면이고,
    // 콘솔이 매번 붉으면 사람은 그것을 안 보게 된다. `issues.spec.ts` 넷이
    // 이걸로 붉어져 드러났다.
    //
    // 처음에는 아래 `consoleErrors` 단언만 두었다. **그건 아무 것도 지키지
    // 않았다** — 그 단언은 그 순간의 배열을 보는 것이고 재시도가 없어서,
    // 답이 도착하기 전에 테스트가 끝나고 컨텍스트가 닫히면 요청 자체가
    // 취소된다. 404 를 되돌려 놓고도 통과했다. 그래서 상태를 직접 본다.
    const asked = page.waitForResponse((r) => r.url().includes('/api/v1/tickets/'))
    await createIssue(page, key, 'plain issue')
    expect((await asked).status(), '티켓이 아니라는 답도 200 이어야 한다').toBe(200)

    await expect(page.getByLabel(/^internal note$/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /reply to customer/i })).toHaveCount(0)
    expect(consoleErrors).toEqual([])
  })
})

test.describe('상담원이 보는 요청 정보', () => {
  test('누가 어느 창구로 냈는지 보여 준다', async ({ page }) => {
    const filed = await fileRequest(page, '요청 정보 확인용')
    await page.goto(`/issues/${filed.issueKey}`)

    // 상담원이 먼저 알아야 하는 것: 누가, 어느 창구로.
    await expect(page.getByText(/^requester$/i)).toBeVisible()
    await expect(page.getByRole('link', { name: `/portal/${filed.slug}` })).toBeVisible()
    await expect(page.getByText(/^came in via$/i)).toBeVisible()
    await filed.context.close()
  })

  test('게스트 주소에는 검증되지 않았다고 적는다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: true })

    const guest = await freshContext(page)
    const anon = await guest.newPage()
    await anon.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await anon.getByLabel(new RegExp(portal.formLabel)).fill('게스트 요청')
    await anon.getByLabel(/your name/i).fill('학부모')
    await anon.getByLabel(/your email/i).fill('parent@school.example.com')

    // **키는 응답에서 읽는다.** 접수 확인 문구에서 정규식으로 뽑으면 문구가
    // 계약이 되고, 문구를 다듬는 순간 스펙이 깨진다.
    const created = anon.waitForResponse(
      (response) =>
        response.url().includes('/guest-requests') && response.request().method() === 'POST',
    )
    await anon.getByRole('button', { name: /send request/i }).click()
    const body = (await (await created).json()) as { key: string }
    // 게스트에게는 요청 번호가 유일한 손잡이다. 화면에도 떠야 한다.
    await expect(anon.getByText(/request number/i)).toContainText(body.key)
    await guest.close()

    await page.goto(`/issues/${body.key}`)
    // 게스트가 적어 낸 주소는 아무나 적을 수 있다. 계정 주소처럼 믿고
    // 확인 없이 무엇을 보내면, 남의 주소를 적은 사람이 그 사람에게 배달한다.
    await expect(page.getByText('parent@school.example.com')).toBeVisible()
    await expect(page.getByText(/unverified address/i)).toBeVisible()
  })
})

test.describe('고객의 회신', () => {
  test('고객이 답하면 상담원 쪽에도 뜬다', async ({ page }) => {
    const filed = await fileRequest(page, '회신 확인용')

    await filed.customerPage.getByLabel(/add a message/i).fill('아직 안 됩니다')
    await filed.customerPage.getByRole('button', { name: /^send$/i }).click()
    await expect(filed.customerPage.getByText('아직 안 됩니다')).toBeVisible()

    // 상담원이 못 보면 티켓이 멈춘다.
    await page.goto(`/issues/${filed.issueKey}`)
    await expect(page.getByText('아직 안 됩니다')).toBeVisible()
    await filed.context.close()
  })
})
