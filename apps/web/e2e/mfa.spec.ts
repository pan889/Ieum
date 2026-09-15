/**
 * 2단계 인증 화면 (auth.md 3절).
 *
 * 이 화면은 스위트 곳곳이 `signInWithMfa` 로 지나간다. 그런데 그 픽스처는
 * **백업 코드**로 지나간다 — 인증기 코드는 한 계정이 30초에 한 번밖에 못
 * 쓰므로, 스무 곳이 그것을 쓰면 스위트가 기다리는 데 분 단위를 버린다.
 *
 * 그래서 **제품의 기본 경로**(인증기 앱의 여섯 자리)는 여기서 본다. 픽스처가
 * 빨라졌다는 이유로 사람들이 실제로 쓰는 길이 시험되지 않으면 안 된다. 틀린
 * 코드를 거절하는 것도 같은 자리에서 본다 — 조용히 통과시키면 2차 요소가
 * 있으나 마나다.
 */

import type { Page } from '@playwright/test'

import { MFA_ADMIN, authenticatorCode, expect, test } from './fixtures'

// 인증기 코드는 앞선 시험이 방금 쓴 30초 창을 피해 기다릴 수 있다.
test.slow()

/** 비밀번호까지만 넣는다. 2단계 화면에서 멈추는 것이 이 헬퍼의 계약이다. */
async function stopAtSecondStep(page: Page): Promise<void> {
  await page.goto('/')
  const email = page.getByLabel(/email|이메일/i)
  const out = page.getByRole('button', { name: /sign out|로그아웃/i })
  await expect(email.or(out).first()).toBeVisible()
  if (await out.isVisible()) {
    await out.click()
    await expect(email).toBeVisible()
  }

  await email.fill(MFA_ADMIN.email)
  await page.getByLabel(/password|비밀번호/i).fill(MFA_ADMIN.password)
  await page.getByRole('button', { name: /^(sign in|로그인)$/i }).click()

  // 비밀번호만으로는 못 들어간다 — 이것이 이 계정의 성질이다.
  await expect(page.getByLabel(/authentication code|인증 코드/i)).toBeVisible()
  await expect(out).toHaveCount(0)
}

test.describe('2단계 인증', () => {
  test('인증기 앱의 코드로 들어온다', async ({ page }) => {
    await stopAtSecondStep(page)

    const code = page.getByLabel(/authentication code|인증 코드/i)
    const digits = await authenticatorCode(page)
    // 제어 컴포넌트라 한 글자씩 넣고 들어간 것을 확인한다. `fill` 로는 값이
    // 상태에 안 들어가 빈 코드가 날아간 적이 있다.
    await code.pressSequentially(digits)
    await expect(code).toHaveValue(digits)
    await page.getByRole('button', { name: /^(verify|확인)$/i }).click()

    await expect(page.getByRole('button', { name: /sign out|로그아웃/i })).toBeVisible()
  })

  test('틀린 코드는 거절하고 그렇다고 말한다', async ({ page }) => {
    await stopAtSecondStep(page)

    const code = page.getByLabel(/authentication code|인증 코드/i)
    // 여섯 자리이되 맞을 리 없는 값. 길이가 틀리면 서버까지 못 가고 폼이
    // 막으므로, **서버가 거절하는 것**을 보려면 모양은 맞아야 한다.
    await code.pressSequentially('000000')
    await page.getByRole('button', { name: /^(verify|확인)$/i }).click()

    await expect(page.getByText(/code isn't right|코드가 올바르지/i)).toBeVisible()
    // 그리고 들여보내지 않았다.
    await expect(page.getByRole('button', { name: /sign out|로그아웃/i })).toHaveCount(0)
  })

  test('백업 코드 칸으로 갈아탈 수 있다', async ({ page }) => {
    // 인증기를 집에 두고 온 사람의 길이다. 칸이 바뀌는 것까지가 제품이다 —
    // 여섯 자리 숫자 칸에 `ABCDE-FGHIJ` 를 넣으라고 하면 안 된다.
    await stopAtSecondStep(page)

    await page.getByRole('button', { name: /use a backup code|백업 코드로 인증/i }).click()
    await expect(page.getByLabel(/backup code|백업 코드/i)).toBeVisible()
    await expect(page.getByLabel(/authentication code|인증 코드/i)).toHaveCount(0)

    // 되돌아올 수도 있어야 한다. 한 번 누르면 못 나오는 길은 길이 아니다.
    await page.getByRole('button', { name: /use your authenticator|인증 앱으로 인증/i }).click()
    await expect(page.getByLabel(/authentication code|인증 코드/i)).toBeVisible()
  })
})
