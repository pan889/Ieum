/**
 * 데스크 리포트 — SLA 성적과 상담원별 성적 (feature-map C14).
 *
 * 서버 시험 24개가 세는 규칙을 이미 붙잡는다. 브라우저로 한 번 더 보는
 * 이유는 **숫자가 오해되지 않게 놓였는가**이고, 그건 눈으로만 보인다:
 *
 * - **`늦게 끝남`과 `지금 넘김`이 따로 보인다.** 앞은 이미 벌어진 일이고
 *   뒤는 지금 손쓸 수 있는 일이다. 한 칸으로 합치면 이 화면을 여는 이유가
 *   사라진다.
 * - **위반을 어떻게 셌는지 화면이 말한다.** "알림이 안 왔는데 왜 위반인가"
 *   의 답이 코드에만 있으면 아무도 못 읽는다.
 * - **담당자 없음이 이름 붙은 칸으로 보인다.** 빈 칸으로 두면 표에 이유
 *   없는 구멍이 생기고, 보통 가장 봐야 할 무리가 그 구멍에 들어간다.
 * - **하나도 못 끝낸 사람의 평균은 `—` 다.** `0` 으로 그리면 그 사람이 가장
 *   빠른 사람으로 보인다.
 * - **평균 옆에 그것이 벽시계라고 적혀 있다.** SLA 목표와 나란히 비교하면
 *   그 비교는 언제나 틀린다.
 */

import type { Page } from '@playwright/test'

import {
  createPortal,
  expect,
  inviteUser,
  signIn,
  signInAsCustomer,
  signInWithMfa,
  test,
  type PortalFixture,
} from './fixtures'

test.slow()

/** 언제나 열린 달력. 업무 시간 계산은 서버 시험 26개가 이미 본다. */
const ALWAYS_OPEN = [0, 1, 2, 3, 4, 5, 6].map((d) => `${String(d)} 00:00-23:59`).join('\n')

/** 고객이 요청을 하나 낸다. 티켓 = 이슈이므로 이것이 리포트의 재료다. */
async function fileRequest(page: Page, portal: PortalFixture, summary: string): Promise<void> {
  const customer = await inviteUser(page, { isCustomer: true })
  const context = await page.context().browser()?.newContext()
  const customerPage = await (context as NonNullable<typeof context>).newPage()
  await signInAsCustomer(customerPage, customer)
  await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
  await customerPage.getByLabel(new RegExp(portal.formLabel)).fill(summary)
  await customerPage.getByRole('button', { name: /send request/i }).click()
  await expect(customerPage.getByRole('heading', { level: 1 })).toContainText(summary)
  await (context as NonNullable<typeof context>).close()
}

/** 리포트 화면을 열고 이 프로젝트로 맞춘다. */
async function openReport(page: Page, portal: PortalFixture): Promise<void> {
  await page.goto('/desk/reports')
  await expect(page.getByRole('heading', { name: /desk report/i, level: 1 })).toBeVisible()
  await page.getByRole('button', { name: /change|choose/i }).click()
  await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
  await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()
  await page.getByRole('button', { name: /^show$/i }).click()
}

test('큐 화면에서 리포트로 갈 수 있다', async ({ page, consoleErrors }) => {
  // 리포트는 **설정이 아니라 작업 화면**이다. 설정 목록에만 두면 팀장이
  // 매주 여는 자리를 찾으려고 설정을 뒤지게 된다.
  await signIn(page)
  await page.goto('/desk')
  await page.getByRole('link', { name: /desk report/i }).click()
  await page.waitForURL(/\/desk\/reports/)
  await expect(page.getByRole('heading', { name: /desk report/i, level: 1 })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('담당자 없는 티켓이 이름 붙은 칸으로 보이고, 평균은 — 다', async ({
  page,
  consoleErrors,
}) => {
  await signIn(page)
  const portal = await createPortal(page)
  await fileRequest(page, portal, 'nobody picked this up')

  await signIn(page)
  await openReport(page, portal)

  await expect(page.getByTestId('desk-report-tickets')).toHaveText(/1 ticket/i)
  const agents = page.getByTestId('desk-report-agents')
  // **빈 칸이 아니라 이름이다.**
  await expect(agents.getByRole('rowheader', { name: /^unassigned$/i })).toBeVisible()
  // 하나도 안 끝났으므로 평균은 `—` 다 — `0` 이면 가장 빠른 사람이 된다.
  await expect(agents.getByTestId('agent-average')).toHaveText('—')
  // 그리고 몇 건 중 몇 건인지 적혀 있다.
  await expect(agents.getByText('0 of 1')).toBeVisible()
  // 평균이 벽시계라는 것도 적혀 있다 — SLA 목표와 비교하면 안 되니까.
  await expect(page.getByText(/business calendar/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('아직 안 알린 위반도 "지금 넘김" 으로 보이고, 어떻게 셌는지 말한다', async ({
  page,
  consoleErrors,
}) => {
  // **이 시험이 이 파일의 이유다.** `breached_at` 으로 세면 스윕이 훑기 전의
  // 위반이 안 보이고, 안 보이는 방향이 하필 듣기 좋은 방향이다. 목표를 1분
  // 으로 두고 지나가길 기다려 그 자리를 실제로 만든다.
  await signInWithMfa(page)
  const portal = await createPortal(page)

  await page.goto('/settings/sla')
  await expect(page.getByRole('heading', { name: /sla policies/i, level: 1 })).toBeVisible()
  await page.getByRole('button', { name: /new calendar/i }).click()
  await page.getByLabel(/^calendar name$/i).fill(`Always ${String(Date.now()).slice(-6)}`)
  await page.getByLabel(/^working hours$/i).fill(ALWAYS_OPEN)
  await page.getByRole('button', { name: /^save$/i }).click()

  await page.getByRole('button', { name: /change|choose/i }).click()
  await page.getByRole('textbox', { name: /^calendar$/i }).fill(portal.projectKey)
  await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()

  await page.getByRole('button', { name: /new policy/i }).click()
  await page.getByLabel(/^policy name$/i).fill('일 분 안에')
  await page.getByLabel(/^targets$/i).fill('1m')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText('일 분 안에')).toBeVisible()

  await fileRequest(page, portal, 'this will go overdue')

  // 워커가 클럭을 걸고(아웃박스 15초), 목표 1분이 지나야 넘김이 된다.
  await signIn(page)
  await expect(async () => {
    await openReport(page, portal)
    const sla = page.getByTestId('desk-report-sla')
    await expect(sla.getByTestId('sla-overdue')).toHaveText('1', { timeout: 3000 })
  }).toPass({ timeout: 150_000 })

  const sla = page.getByTestId('desk-report-sla')
  // 위반 합에도 들어간다 — 화면이 더하지 않고 서버가 준 값이다.
  await expect(sla.getByTestId('sla-breached')).toHaveText('1')
  // **`늦게 끝남` 과 따로 있다.** 합쳐 놓으면 지금 손쓸 것이 안 보인다.
  await expect(page.getByRole('columnheader', { name: /^missed$/i })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: /^overdue now$/i })).toBeVisible()
  // 그리고 위반을 무엇으로 셌는지 화면이 말한다.
  await expect(page.getByText(/not by whether a notification went out/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
