/**
 * SLA 정책·업무 달력과 티켓의 잔여 시간 (feature-map C4·C5).
 *
 * 서버 시험 90개가 규칙을 이미 붙잡고 있다. 브라우저로 한 번 더 보는 이유:
 *
 * - **step-up 이 걸린 화면이다.** 2FA 를 통과하지 않은 세션에는 목록조차
 *   주지 않고, 화면은 "권한 없음" 이 아니라 그 사실을 보여 줘야 한다.
 * - **목표를 JSON 으로 적게 하지 않는다.** 관리자가 중괄호를 틀려서 거절
 *   당하고 이유를 모르는 것이 이 화면이 막아야 하는 것이다.
 * - **잔여 시간은 서버가 계산한다.** 화면이 목표 시각에서 세면 업무 시간이
 *   빠지는데, 그건 눈으로 봐야 드러난다.
 */

import type { Page } from '@playwright/test'

import { createPortal, expect, signIn, signInWithMfa, signInAsCustomer, test } from './fixtures'

test.slow()

const HOURS = '0 09:00-18:00\n1 09:00-18:00\n2 09:00-18:00\n3 09:00-18:00\n4 09:00-18:00'

/** SLA 설정 화면. **2FA 를 통과한 관리자로** 들어간다. */
async function openSla(page: Page): Promise<void> {
  await signInWithMfa(page)
  await page.goto('/settings/sla')
  await expect(page.getByRole('heading', { name: /sla policies/i, level: 1 })).toBeVisible()
}

async function addCalendar(page: Page, name: string, hours = HOURS): Promise<void> {
  await page.getByRole('button', { name: /new calendar/i }).click()
  await page.getByLabel(/^name$/i).fill(name)
  await page.getByLabel(/^working hours$/i).fill(hours)
  await page.getByRole('button', { name: /^save$/i }).click()
}

test.describe('업무 달력', () => {
  test('만들면 목록에 뜨고 업무 시간을 보여 준다', async ({ page }) => {
    await openSla(page)
    const name = `Seoul ${String(Date.now()).slice(-6)}`
    await addCalendar(page, name)

    await expect(page.getByText(name)).toBeVisible()
    // 시간표를 숨기지 않는다 — SLA 가 뜻밖일 때 사람이 볼 것은 이것이다.
    await expect(page.getByText(/09:00-18:00/).first()).toBeVisible()
  })

  test('읽을 수 없는 업무 시간으로는 저장을 막고 형식을 말한다', async ({ page }) => {
    // **저장하고 거절당하게 두지 않는다.** 서버도 막지만, 여기서 막으면
    // 무엇이 문제인지 그 자리에서 보인다.
    await openSla(page)
    await page.getByRole('button', { name: /new calendar/i }).click()
    await page.getByLabel(/^name$/i).fill('broken')
    await page.getByLabel(/^working hours$/i).fill('월요일 아침부터 저녁까지')

    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()
    // 그리고 어떤 모양이어야 하는지 화면이 말한다.
    await expect(page.getByText(/Mon=0/).first()).toBeVisible()
  })
})

test.describe('SLA 정책', () => {
  test('목표를 사람이 읽는 말로 적고, 어느 달력으로 재는지 보여 준다', async ({ page }) => {
    await openSla(page)
    const calendar = `Seoul ${String(Date.now()).slice(-6)}`
    await addCalendar(page, calendar)

    await page.getByRole('button', { name: /new policy/i }).click()
    // 정책 폼의 이름 칸은 두 번째다(달력 폼이 닫혀 있어도 목록의 이름과
    // 겹치지 않게 폼 안에서 고른다).
    await page.getByLabel(/^name$/i).fill('첫 응답')
    // **초가 아니라 `4h` 다.** 초로 받으면 사람이 14400 을 계산해야 하고
    // 그 계산은 틀린다.
    await page.getByLabel(/^targets$/i).fill('priority>=5 1h\n4h')
    await page.getByRole('button', { name: /^save$/i }).click()

    await expect(page.getByText('첫 응답')).toBeVisible()
    await expect(page.getByText(/first response/i).first()).toBeVisible()
    // 어느 달력으로 재는지 목록에 있다 — 서버가 이름을 함께 준다.
    await expect(page.getByText(calendar).first()).toBeVisible()
    await expect(page.getByText(/1h · 4h|1h.*4h/).first()).toBeVisible()
  })

  test('읽을 수 없는 목표로는 저장을 막는다', async ({ page }) => {
    await openSla(page)
    await addCalendar(page, `Seoul ${String(Date.now()).slice(-6)}`)
    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('틀린 목표')
    await page.getByLabel(/^targets$/i).fill('네 시간쯤')

    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()
  })

  test('고칠 때는 재는 것을 바꿀 수 없고 이유를 말한다', async ({ page }) => {
    // 이미 걸린 시계가 다른 것을 재게 되므로 서버가 그 필드를 아예 받지
    // 않는다 — 화면도 손잡이를 그리지 않아야 한다.
    await openSla(page)
    const calendar = `Seoul ${String(Date.now()).slice(-6)}`
    await addCalendar(page, calendar)
    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('해결')
    await page.getByLabel(/^measures$/i).selectOption('resolution')
    await page.getByLabel(/^targets$/i).fill('3d')
    await page.getByRole('button', { name: /^save$/i }).click()
    await expect(page.getByText('해결')).toBeVisible()

    await page.getByRole('button', { name: /^edit$/i }).last().click()
    // 선택 상자가 없다.
    await expect(page.getByLabel(/^measures$/i)).toHaveCount(0)
    // 그리고 왜 없는지 적혀 있다.
    await expect(page.getByText(/cannot be changed/i)).toBeVisible()
  })

  test('멈춤 상태는 고르는 것이고, 고른 것이 목록에 남는다', async ({ page }) => {
    // **UUID 를 손으로 적게 하지 않는다.** 서버가 모르는 상태를 거절하므로,
    // 화면이 고를 수 있는 것을 보여 주지 않으면 관리자는 거절만 당하고
    // 무엇을 고를 수 있는지 모른다.
    //
    // 그리고 **고른 것이 목록 줄에 보인다.** 안 보이면 멈춤을 걸어 뒀는지
    // 폼을 열어 봐야 알고, 안 걸린 정책과 구별되지 않는다.
    await openSla(page)
    const calendar = `Seoul ${String(Date.now()).slice(-6)}`
    await addCalendar(page, calendar)

    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('멈춤 확인용')
    await page.getByLabel(/^targets$/i).fill('4h')

    // 프로젝트의 상태가 체크박스로 온다 — 이름을 적는 칸이 아니다.
    const paused = page.getByRole('checkbox', { name: /In Progress/ })
    await expect(paused).toBeVisible()
    await paused.check()
    await page.getByRole('button', { name: /^save$/i }).click()

    // **그 정책의 줄에서** 본다. 목록은 이름순이고 앞선 시험들이 만든
    // 정책이 섞여 있어서, `.last()` 로 집으면 남의 줄을 연다 — 처음에 그렇게
    // 적었고, 통과해야 할 시험이 엉뚱한 줄을 보고 붉어졌다.
    const row = page.locator('li').filter({ hasText: '멈춤 확인용' })
    await expect(row).toBeVisible()
    // 목록 줄이 무엇을 걸었는지 말한다.
    await expect(row.getByText(/Pause in these states:.*In Progress/i)).toBeVisible()

    // **다시 열면 체크가 남아 있다.** 서버가 되돌려준 id 를 폼이 받아
    // 표시하지 않으면, 고치기 한 번으로 멈춤 설정이 지워진다.
    await row.getByRole('button', { name: /^edit$/i }).click()
    await expect(page.getByRole('checkbox', { name: /In Progress/ })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: /^Open/ })).not.toBeChecked()
  })

  test('에스컬레이션 규칙을 손잡이로 적고, 목록에 남는다', async ({ page }) => {
    // **JSON 이 아니라 손잡이다.** 조치가 둘이고 각각 다른 것을 필요로 한다
    // (누구에게 / 몇으로) — 자유 입력으로 두면 서버가 거절하는 조합을 만들 수
    // 있고, 관리자는 무엇이 틀렸는지 모른다.
    await openSla(page)
    await addCalendar(page, `Seoul ${String(Date.now()).slice(-6)}`)

    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('에스컬레이션 확인용')
    await page.getByLabel(/^targets$/i).fill('4h')

    await page.getByRole('button', { name: /add rule/i }).click()
    await page.getByLabel(/% of target/i).fill('120')
    await page.getByLabel(/^raise to$/i).selectOption('5')
    await page.getByRole('button', { name: /^save$/i }).click()

    const row = page.locator('li').filter({ hasText: '에스컬레이션 확인용' })
    await expect(row).toBeVisible()
    await expect(row.getByText(/1 escalations/i)).toBeVisible()

    // 다시 열면 적은 값이 그대로 있다.
    await row.getByRole('button', { name: /^edit$/i }).click()
    await expect(page.getByLabel(/% of target/i)).toHaveValue('120')
    await expect(page.getByLabel(/^action$/i)).toHaveValue('raise_priority')
  })

  test('부를 사람을 안 고른 규칙으로는 저장을 막는다', async ({ page }) => {
    // 서버도 거절한다. 여기서 막으면 어느 줄이 문제인지 그 자리에서 보인다 —
    // 저장을 눌러 거절당하고 나서 다섯 줄 중 어디인지 찾게 두지 않는다.
    await openSla(page)
    await addCalendar(page, `Seoul ${String(Date.now()).slice(-6)}`)

    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('사람 없는 규칙')
    await page.getByLabel(/^targets$/i).fill('4h')
    await page.getByRole('button', { name: /add rule/i }).click()
    // 기본 조치는 우선순위 올리기다 — 그것만으로는 저장할 수 있다.
    await expect(page.getByRole('button', { name: /^save$/i })).toBeEnabled()

    await page.getByLabel(/^action$/i).selectOption('notify')
    // 이제 부를 사람이 없다.
    await expect(page.getByRole('button', { name: /^save$/i })).toBeDisabled()
    await expect(page.getByText(/who to call/i).first()).toBeVisible()
  })
})

test.describe('step-up', () => {
  test('2FA 를 통과하지 않으면 목록조차 주지 않는다', async ({ page }) => {
    // **"권한 없음" 이 아니다.** 권한은 있고 증명이 없는 것이므로 화면이
    // 그 차이를 보여 줘야 한다 — 서버가 `auth.mfa_required` 로 답한다.
    await signIn(page)
    await page.goto('/settings/sla')
    await expect(page.getByRole('heading', { name: /sla policies/i, level: 1 })).toBeVisible()
    await expect(page.getByRole('alert').first()).toBeVisible()
  })
})

test.describe('티켓의 잔여 시간', () => {
  test('정책이 걸린 티켓에 남은 업무 시간이 뜬다', async ({ page }) => {
    // **이 시험이 이 파일의 이유다.** 클럭은 워커가 건다 — 그 배선이
    // 끊기면 서버 시험은 통과하는데 화면의 SLA 칸은 영원히 비어 있다.
    await openSla(page)
    const calendar = `Seoul ${String(Date.now()).slice(-6)}`
    // 언제나 열린 달력으로 둔다. 업무 시간 계산은 서버 시험 26개가 이미
    // 본다 — 여기서 보는 것은 값이 화면까지 오는가다.
    await addCalendar(page, calendar, [0, 1, 2, 3, 4, 5, 6].map((d) => `${String(d)} 00:00-23:59`).join('\n'))

    const portal = await createPortal(page)
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^calendar$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()

    await page.getByRole('button', { name: /new policy/i }).click()
    await page.getByLabel(/^name$/i).fill('첫 응답')
    await page.getByLabel(/^targets$/i).fill('4h')
    await page.getByRole('button', { name: /^save$/i }).click()
    await expect(page.getByText('첫 응답')).toBeVisible()

    // 고객이 요청을 낸다.
    const customer = await import('./fixtures').then((m) =>
      m.inviteUser(page, { isCustomer: true }),
    )
    const context = await page.context().browser()?.newContext()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('SLA 확인용')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('SLA 확인용')
    const shown = await customerPage.locator('body').innerText()
    const issueKey = (/\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown) as RegExpExecArray)[1] as string
    await (context as NonNullable<typeof context>).close()

    // **워커가 클럭을 걸 때까지 기다린다.** 아웃박스를 15초마다 훑으므로
    // 즉시 뜨지 않는다 — 그 사실 자체가 이 시험이 확인하는 것의 일부다.
    await signIn(page)
    await expect(async () => {
      await page.goto(`/issues/${issueKey}`)
      await expect(page.getByText(/left$|남음/).first()).toBeVisible({ timeout: 3000 })
    }).toPass({ timeout: 60_000 })

    await expect(page.getByRole('heading', { name: /^sla$/i })).toBeVisible()
    await expect(page.getByText(/first response/i).first()).toBeVisible()
  })
})
