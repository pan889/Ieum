/**
 * 2FA 강제 정책과 강제 등록 화면 (auth.md 3절).
 *
 * 여기서 잡는 것은 **락아웃**이다. 정책을 켜면 자격증명이 없는 사람은
 * 확인 화면이 아니라 등록 화면으로 가야 한다. 확인 화면으로 보내면 만들 수
 * 없는 코드를 요구받고, 그 계정은 다시 못 들어온다.
 *
 * 서버 테스트로는 응답의 플래그까지만 볼 수 있다. 그 플래그를 화면이 실제로
 * 갈라 쓰는지는 브라우저에서만 드러난다 — 단계 하나를 안 이어 놓아서
 * 등록 화면이 통째로 도달 불가였던 것이 이 과제의 시작이었다.
 */

import { expect, signIn, test } from './fixtures'

/**
 * 조직 전체 스위치라 켜 둔 채 끝나면 뒤따르는 모든 테스트가 등록 화면에
 * 갇힌다. 실패하든 말든 반드시 되돌린다.
 */
test.afterEach(async ({ page }) => {
  await page.goto('/settings/security')
  const off = page.getByRole('button', { name: /^turn off$/i })
  const on = page.getByRole('button', { name: /^turn on$/i })
  // **부팅을 기다린다.** 앱은 새로고침마다 토큰을 다시 받아 오므로 goto 직후
  // 화면은 "Loading…" 뿐이다. 그때 세어 보면 0 이 나오고, 끄지도 않은 채
  // 조용히 지나가 뒤따르는 테스트 전부가 등록 화면에 갇힌다.
  await expect(off.or(on).first()).toBeVisible({ timeout: 15_000 })
  if (await off.count()) {
    await off.click()
    await expect(on).toBeVisible()
  }
})

test('정책을 켜면 등록 화면으로 간다 — 만들 수 없는 코드를 묻지 않는다', async ({
  page,
  browser,
  consoleErrors,
}) => {
  await signIn(page)
  await page.goto('/settings/security')

  await page.getByRole('button', { name: /^turn on$/i }).click()
  await expect(page.getByRole('button', { name: /^turn off$/i })).toBeVisible()
  // 이미 열려 있는 세션은 그대로 산다. 안 그러면 관리자 자신도 못 되돌린다.
  await expect(page.getByText(/sessions that are already open keep working/i)).toBeVisible()
  await expect(page.getByRole('link', { name: /audit log/i })).toBeVisible()

  const other = await browser.newContext()
  const fresh = await other.newPage()
  // 공용 `signIn` 은 앱 셸을 기다린다. 여기서는 셸이 안 뜨는 게 정상이다.
  await fresh.goto('/')
  await fresh.getByLabel(/email|이메일/i).fill('admin@example.com')
  await fresh.getByLabel(/password|비밀번호/i).fill('seed-admin-password-1234')
  await fresh.getByRole('button', { name: /^(sign in|로그인)$/i }).click()

  // 등록 화면이다. "코드를 넣으라" 가 아니라 "등록하라".
  await expect(fresh.getByText(/set up two-factor authentication/i)).toBeVisible()
  await expect(fresh.getByRole('button', { name: /confirm and enable/i })).toBeVisible()
  // QR 이 실제로 그려져야 한다 — 주소만 맞고 빈 상자인 경우가 있다.
  await expect(fresh.locator('svg, img').first()).toBeVisible()
  await other.close()

  expect(consoleErrors).toEqual([])
})

test('정책이 꺼져 있으면 아무도 등록을 강요받지 않는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/security')
  await expect(page.getByRole('button', { name: /^turn on$/i })).toBeVisible()

  await page.goto('/projects')
  await expect(page.getByRole('heading', { name: /projects/i })).toBeVisible()

  expect(consoleErrors).toEqual([])
})
