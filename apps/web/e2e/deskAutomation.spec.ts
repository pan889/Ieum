/**
 * 자동화 규칙 화면 (feature-map C9).
 *
 * 서버 시험이 규칙 언어와 실행을 이미 붙잡고 있다. 브라우저로 한 번 더 보는
 * 이유:
 *
 * - **자동화의 실패는 조용하다.** 규칙이 안 돈 것과 조건이 안 맞은 것이 화면에
 *   똑같이 "아무 일도 안 일어남" 으로 보인다. 그래서 잘못된 규칙을 **저장하기
 *   전에** 막는지가 다른 화면보다 무겁다.
 * - **값 칸이 항목마다 달라야 한다.** 전부 글자 입력이면 `is_internal` 에
 *   `"true"` 를 적을 수 있고, 그 규칙은 저장되고 한 번도 안 걸린다. 이건
 *   그리는 쪽의 성질이라 서버 시험이 못 본다.
 * - **UUID 를 손으로 적게 하지 않는다.** 요청 유형 목록은 다른 권한이 지키는
 *   목록인데, 자동화 화면에서 고를 수 있어야 한다.
 * - **step-up 이 걸린 화면이다.** 규칙은 사람이 안 보는 사이에 고객에게 글을
 *   보낸다.
 * - **규칙이 실제로 돈다.** 화면 → 워커 → 티켓까지 이어지는지는 여기서만 본다.
 */

import type { Page } from '@playwright/test'

import {
  createPortal,
  expect,
  signIn,
  signInAsCustomer,
  signInWithMfa,
  inviteUser,
  test,
  uniqueKey,
} from './fixtures'

test.slow()

/** 자동화 화면. **2FA 를 통과한 관리자로** 들어간다. */
async function openAutomation(page: Page, projectKey?: string): Promise<void> {
  await signInWithMfa(page)
  await page.goto('/settings/automation')
  await expect(page.getByRole('heading', { name: /^automation$/i, level: 1 })).toBeVisible()
  if (projectKey !== undefined) {
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(projectKey)
    await page.getByRole('button', { name: new RegExp(projectKey) }).click()
  }
}

test.describe('자동화 규칙', () => {
  test('규칙을 만들면 목록에 무엇을 하는 규칙인지 적혀 있다', async ({ page }) => {
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    const name = uniqueKey('긴급 올리기 ')
    await page.getByLabel(/^name$/i).fill(name)
    await page.getByLabel(/^when$/i).selectOption({ label: 'A ticket comes in' })

    await page.getByRole('button', { name: /add condition/i }).click()
    await page.getByLabel(/^field$/i).selectOption({ label: 'Came in via' })
    await page.getByLabel(/^value$/i).selectOption({ label: 'Email' })

    await page.getByLabel(/^new priority$/i).selectOption('5')
    await page.getByRole('button', { name: /^save$/i }).click()

    // 폼을 열어 봐야 아는 규칙은 목록이 아니다.
    const row = page.locator('li').filter({ hasText: name })
    await expect(row).toBeVisible()
    await expect(row.getByText('A ticket comes in')).toBeVisible()
    await expect(row.getByText(/1 condition/)).toBeVisible()
    await expect(row.getByText(/priority to 5/)).toBeVisible()
  })

  test('참·거짓 항목의 값은 고르는 것이지 적는 것이 아니다', async ({ page }) => {
    // 글자 `"true"` 를 적을 수 있으면 그 규칙은 저장되고 한 번도 안 걸린다 —
    // 서버는 참·거짓과 글자를 같다고 보지 않는다.
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByRole('button', { name: /add condition/i }).click()

    // 숫자 항목으로 시작한다 — 고르는 칸이다.
    await expect(page.getByLabel(/^value$/i)).toHaveRole('combobox')
    await page.getByLabel(/^field$/i).selectOption({ label: 'Is an internal note' })
    const value = page.getByLabel(/^value$/i)
    await expect(value).toHaveRole('combobox')
    await expect(value.getByRole('option')).toHaveText(['Choose one', 'Yes', 'No'])

    // 글자 항목으로 바꾸면 그때는 적는 칸이 된다.
    await page.getByLabel(/^field$/i).selectOption({ label: 'Summary' })
    await expect(page.getByLabel(/^value$/i)).toHaveRole('textbox')
  })

  test('항목을 바꾸면 쓸 수 없는 비교가 사라진다', async ({ page }) => {
    // `summary >= 4` 는 서버가 거절하고, 예전에는 저장되고 안 맞았다.
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByRole('button', { name: /add condition/i }).click()
    await expect(page.getByLabel(/^compare$/i).getByRole('option')).toContainText([
      'is',
      'is not',
      'is at least',
    ])

    await page.getByLabel(/^field$/i).selectOption({ label: 'Summary' })
    await expect(page.getByLabel(/^compare$/i).getByRole('option')).toHaveText([
      'is',
      'is not',
      'contains',
    ])
  })

  test('요청 유형은 UUID 가 아니라 이름으로 고른다', async ({ page }) => {
    // 이 목록은 `desk.portal.manage` 가 지킨다. 규칙을 쓰는 사람에게 그것을
    // 마저 요구하면, 고를 수는 없는데 적으면 되는 자리가 된다.
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByRole('button', { name: /add condition/i }).click()
    await page.getByLabel(/^field$/i).selectOption({ label: 'Request form' })

    const value = page.getByLabel(/^value$/i)
    await expect(value.getByRole('option', { name: /Broken thing/ })).toHaveCount(1)
    // 아무 것도 안 골랐으므로 저장이 막혀 있다 — 우연히 첫째가 걸린 규칙을
    // 저장하게 두지 않는다.
    await page.getByLabel(/^name$/i).fill('폼으로 거르기')
    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()

    // `label` 은 정확한 글자만 받는다(정규식이 아니다). 순번으로 고른다 —
    // 0 은 안내 항목이다.
    await value.selectOption({ index: 1 })
    await expect(page.getByRole('button', { name: /^save$/i })).toBeEnabled()
  })

  test('"다음 중 하나" 는 여러 개를 고르는 상자가 된다', async ({ page }) => {
    // 목록으로 보내야 하는 값이다. 글자 하나로 보내면 서버가 거절한다.
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    const name = uniqueKey('둘 중 하나 ')
    await page.getByLabel(/^name$/i).fill(name)
    await page.getByRole('button', { name: /add condition/i }).click()
    await page.getByLabel(/^field$/i).selectOption({ label: 'Came in via' })
    await page.getByLabel(/^compare$/i).selectOption({ label: 'is one of' })

    const value = page.getByLabel(/^value$/i)
    await expect(value).toHaveAttribute('multiple', '')
    await value.selectOption(['portal', 'email'])
    await page.getByRole('button', { name: /^save$/i }).click()

    const row = page.locator('li').filter({ hasText: name })
    await expect(row).toBeVisible()
    await expect(row.getByText(/1 condition/)).toBeVisible()

    // 고친 것을 다시 열면 고른 둘이 그대로 있다 — 저장한 것이 목록이라는
    // 뜻이다. 글자 하나로 저장됐으면 하나만 골라져 있다.
    await row.getByRole('button', { name: /^edit$/i }).click()
    await expect(page.getByLabel(/^value$/i)).toHaveValues(['portal', 'email'])
  })

  test('지목하지 않은 조치는 저장을 막는다', async ({ page }) => {
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByLabel(/^name$/i).fill('아무에게나')
    // 우선순위 조치는 기본값이 있어 저장할 수 있다.
    await expect(page.getByRole('button', { name: /^save$/i })).toBeEnabled()

    // 사람을 안 고른 배정으로 바꾸면 막힌다.
    await page.getByLabel(/^action$/i).selectOption({ label: 'Assign' })
    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()
  })

  test('멈춘 규칙은 그렇다고 적혀 있고 다시 켤 수 있다', async ({ page }) => {
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    const name = uniqueKey('잠깐 멈춤 ')
    await page.getByLabel(/^name$/i).fill(name)
    await page.getByRole('button', { name: /^save$/i }).click()

    const row = page.locator('li').filter({ hasText: name })
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: /^pause$/i }).click()
    await expect(row.getByText(/^paused$/i)).toBeVisible()
    await row.getByRole('button', { name: /^resume$/i }).click()
    await expect(row.getByText(/^paused$/i)).toHaveCount(0)
  })
})

test.describe('규칙이 실제로 돈다', () => {
  test('포털로 들어온 티켓의 우선순위를 규칙이 올린다', async ({ page }) => {
    const portal = await createPortal(page)
    await openAutomation(page, portal.projectKey)

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByLabel(/^name$/i).fill('포털은 급하다')
    await page.getByRole('button', { name: /add condition/i }).click()
    await page.getByLabel(/^field$/i).selectOption({ label: 'Came in via' })
    await page.getByLabel(/^value$/i).selectOption({ label: 'Portal' })
    await page.getByLabel(/^new priority$/i).selectOption('5')
    await page.getByRole('button', { name: /^save$/i }).click()
    await expect(page.getByText('포털은 급하다')).toBeVisible()

    // 고객이 요청을 낸다.
    const customer = await inviteUser(page, { isCustomer: true })
    const context = await page.context().browser()?.newContext()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('자동화 확인용')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('자동화 확인용')
    const shown = await customerPage.locator('body').innerText()
    const issueKey = (/\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown) as RegExpExecArray)[1] as string
    await (context as NonNullable<typeof context>).close()

    // **워커가 아웃박스를 훑을 때까지 기다린다.** 15초마다 돌므로 즉시 안 뜬다.
    await signIn(page)
    await expect(async () => {
      await page.goto(`/issues/${issueKey}`)
      await expect(page.getByLabel(/^priority$/i)).toHaveValue('5', { timeout: 3000 })
    }).toPass({ timeout: 60_000 })
  })
})

test.describe('step-up', () => {
  test('2FA 를 통과하지 않으면 목록조차 주지 않는다', async ({ page }) => {
    // 규칙 하나가 그 뒤로 오는 모든 티켓에 걸리고 고객에게 글을 보낸다.
    await signIn(page)
    await page.goto('/settings/automation')
    await expect(page.getByText(/two-factor|2단계|step/i).first()).toBeVisible()
  })
})
