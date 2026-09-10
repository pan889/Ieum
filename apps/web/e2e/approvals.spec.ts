/**
 * 승인 단계 (feature-map C12).
 *
 * 서버 시험 51개가 판정과 배선을 이미 붙잡고 있다. 브라우저로 한 번 더 보는
 * 이유는 이 기능이 **두 사람의 두 화면**으로 이루어져 있고, 둘이 이어져야만
 * 쓸 수 있기 때문이다:
 *
 * - 관리자가 요청 유형의 줄에서 **승인을 켠다.** 요청 유형에는 편집 폼이
 *   없어서 이 칸만 따로 나 있고, 그게 실제로 저장되는지는 눌러 봐야 안다.
 * - 승인자는 첫 화면에서 **자기 몫을 보고 그 자리에서 결정한다.** 승인자는
 *   그 프로젝트의 이슈를 볼 권한이 없을 수 있으므로(근거가 명단이다),
 *   이슈 상세로 보내는 것만으로는 결정할 길이 없다 — 처음에 그렇게 만들었고
 *   승인자로 로그인해 보니 링크가 403 이었다.
 * - 상담원은 이슈 상세에서 **왜 막혔는지** 본다.
 */

import type { Page } from '@playwright/test'

import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  API,
  createPortal,
  expect,
  inviteUser,
  signIn,
  test,
  type InvitedUser,
} from './fixtures'

test.slow()

/** 관리자 토큰. 준비는 API 로 하고, 보는 것은 화면으로 한다.
 *
 * **자격은 `fixtures` 에서 받는다.** 여기에 적어 두면 시드 비밀번호를 정해 두는
 * 곳(CI)에서 401 이 된다 — 실제로 이 파일이 그래서 CI 에서만 붉었다.
 */
async function adminToken(page: Page): Promise<string> {
  const signedIn = await page.request.post(`${API}/api/v1/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  })
  expect(signedIn.ok(), await signedIn.text()).toBe(true)
  return ((await signedIn.json()) as { access_token: string }).access_token
}

/** 고객으로 요청을 하나 낸다. 포털 화면은 `portal.spec.ts` 가 본다. */
async function submitRequest(
  page: Page,
  portal: { slug: string; requestTypeId: string },
  customer: InvitedUser,
  summary: string,
): Promise<string> {
  const signedIn = await page.request.post(`${API}/api/v1/auth/login`, {
    data: { email: customer.email, password: customer.password },
  })
  expect(signedIn.ok(), await signedIn.text()).toBe(true)
  const token = ((await signedIn.json()) as { access_token: string }).access_token
  const made = await page.request.post(`${API}/api/v1/portal/${portal.slug}/requests`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { request_type_id: portal.requestTypeId, answers: { summary } },
  })
  expect(made.ok(), await made.text()).toBe(true)
  return ((await made.json()) as { key: string }).key
}

test.describe('승인 단계', () => {
  test('요청 유형의 줄에서 승인을 켜고, 승인자가 첫 화면에서 결정한다', async ({
    page,
    consoleErrors,
  }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: false })
    // **역할을 주지 않는다.** 승인의 근거가 권한이 아니라 명단이라는 것을
    // 이 시험이 보려면 이 사람에게 아무 권한도 없어야 한다.
    const approver = await inviteUser(page)
    const customer = await inviteUser(page, { isCustomer: true })

    // 1. 관리자가 줄에서 승인을 켠다.
    await signIn(page)
    await page.goto('/settings/portals')
    // 피커는 접혀 있다. 먼저 펼치고 키로 찾는다 (`deskKb.spec.ts` 와 같은 순서).
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()
    await page.getByRole('button', { name: /request forms/i }).first().click()

    await page.getByRole('button', { name: /^require approval$/i }).click()
    await page.getByRole('textbox', { name: /pick a person/i }).fill(approver.displayName)
    await page.getByRole('button', { name: approver.displayName }).click()
    await page.getByRole('button', { name: /^save$/i }).click()
    // 저장되면 접히고 방식이 뱃지로 남는다.
    await expect(page.getByText(/any one approver/i).first()).toBeVisible()

    // 2. 고객이 요청을 낸다.
    const issueKey = await submitRequest(page, portal, customer, '회계 계정이 필요합니다')

    // 3. 상담원(관리자)은 착수할 수 없고, **왜 막혔는지** 화면이 말한다.
    await signIn(page)
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByTestId('issue-approvals')).toBeVisible()
    await expect(page.getByText(/waiting for approval/i).first()).toBeVisible()
    await expect(
      page.getByText(new RegExp(`Waiting on ${approver.displayName}`, 'i')),
    ).toBeVisible()

    // 4. 승인자가 **첫 화면에서** 결정한다. 이슈 상세로 갈 권한이 없다.
    await signIn(page, approver)
    const panel = page.getByTestId('home-approvals')
    await expect(panel).toBeVisible()
    await expect(panel.getByText('회계 계정이 필요합니다')).toBeVisible()
    await panel.getByRole('button', { name: /^approve$/i }).click()
    // 결정하면 목록에서 사라진다 — 남은 것이 없으면 칸 자체가 없다.
    await expect(page.getByTestId('home-approvals')).toHaveCount(0)

    // 5. 이제 상담원이 착수할 수 있다.
    await signIn(page)
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByText(/^approved$/i).first()).toBeVisible()
    await page.getByRole('button', { name: /start progress/i }).click()
    await expect(page.getByText('In Progress').first()).toBeVisible()

    expect(consoleErrors).toEqual([])
  })

  test('거절은 이유와 함께 남는다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page, { isPublic: false })
    const approver = await inviteUser(page)
    const customer = await inviteUser(page, { isCustomer: true })

    // 규칙은 API 로 건다 — 화면에서 켜는 것은 위 시험이 이미 본다.
    const token = await adminToken(page)
    const people = await page.request.get(
      `${API}/api/v1/users?q=${encodeURIComponent(approver.email)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    const approverId = ((await people.json()) as { items: { id: string }[] }).items[0]?.id
    expect(approverId, '초대한 승인자를 못 찾았다').toBeTruthy()
    const linked = await page.request.patch(
      `${API}/api/v1/portals/request-types/${portal.requestTypeId}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { approval: { mode: 'one', user_ids: [approverId], group_ids: [] } },
      },
    )
    expect(linked.ok(), await linked.text()).toBe(true)

    const issueKey = await submitRequest(page, portal, customer, '외부 저장소 접근')

    await signIn(page, approver)
    const panel = page.getByTestId('home-approvals')
    await panel.getByRole('textbox', { name: /^note$/i }).fill('보안 검토가 먼저 필요합니다')
    await panel.getByRole('button', { name: /^decline$/i }).click()
    await expect(page.getByTestId('home-approvals')).toHaveCount(0)

    // 상담원이 이유를 읽는다. 그게 요청자에게 설명할 근거다.
    await signIn(page)
    await page.goto(`/issues/${issueKey}`)
    const approvals = page.getByTestId('issue-approvals')
    await expect(approvals.getByText(/declined/i).first()).toBeVisible()
    await expect(approvals.getByText('보안 검토가 먼저 필요합니다')).toBeVisible()
  })
})
