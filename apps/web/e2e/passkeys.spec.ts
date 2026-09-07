/**
 * WebAuthn/패스키 (auth.md 3절).
 *
 * 크로미움의 **가상 인증기**로 돈다. 서명도 검증도 진짜 경로를 타고, 브라우저
 * 쪽 번역(base64url ↔ ArrayBuffer)까지 실제로 실행된다 — 그 번역은 서버
 * 테스트로 볼 수 없다: 서버는 이미 문자열이 된 것만 본다.
 *
 * **테스트마다 사람을 새로 만든다.** 시드 관리자에 인증기를 붙이면 그 계정의
 * 로그인이 그때부터 2FA 를 요구하고, 가상 인증기는 실행마다 새로 만들어져 그
 * 키를 들고 있지 않다 — 한 번 실패하면 되돌릴 수 없는 상태가 남아 다음 실행이
 * 시작조차 못 한다. 실제로 그렇게 만들어 보고 나서 이 모양으로 바꿨다.
 *
 * 자격증명은 **호스트에 묶인다**(rp_id). 그래서 이 스펙만 앱의 정본 주소로
 * 연다 — `127.0.0.1` 로 열면 `localhost` 에 등록한 키가 보이지 않는다.
 */

import type { CDPSession, Page } from '@playwright/test'

import type { InvitedUser } from './fixtures'
import { APP_URL, expect, inviteUser, test } from './fixtures'

/** 사용자 확인을 **하는** 인증기. 서버가 그것을 요구한다. */
async function virtualAuthenticator(cdp: CDPSession): Promise<string> {
  await cdp.send('WebAuthn.enable')
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      // 안 하는 인증기로 통과되면 "꽂혀 있음" 만으로 2차 요소가 되는 셈이다.
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  return authenticatorId
}

async function signInWithPassword(page: Page, who: InvitedUser): Promise<void> {
  await page.getByLabel(/email|이메일/i).fill(who.email)
  await page.getByLabel(/password|비밀번호/i).fill(who.password)
  await page.getByRole('button', { name: /^(sign in|로그인)$/i }).click()
}

test.describe.configure({ mode: 'serial' })
/** 초대 메일을 기다린다. */
test.slow()

test('패스키를 등록하고 그것으로 2차 요소를 통과한다', async ({
  page,
  context,
  consoleErrors,
}) => {
  const invited = await inviteUser(page)
  const authenticatorId = await virtualAuthenticator(await context.newCDPSession(page))

  await page.goto(APP_URL)
  await signInWithPassword(page, invited)
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })

  await page.goto(`${APP_URL}/settings/passkeys`)
  await expect(page.getByText(/nothing registered/i)).toBeVisible()
  await page.getByRole('button', { name: /add a passkey/i }).click()

  const rows = page.getByRole('list', { name: /two-factor/i }).getByRole('listitem')
  await expect(rows).toHaveCount(1, { timeout: 20_000 })
  // **서버가 이름을 지어내지 않는다.** 화면이 종류로 부른다 — 서버가 채우면
  // 영어 화면에 한국어가 섞인다(실제로 그랬다).
  await expect(rows.first()).toContainText(/this device only/i)

  const cdp = await context.newCDPSession(page)
  await cdp.send('WebAuthn.enable')
  const stored = await cdp.send('WebAuthn.getCredentials', { authenticatorId })
  expect(stored.credentials.length).toBe(1)

  // 새 로그인은 미완료 세션이다. 패스키가 그것을 열어야 한다.
  await page.getByRole('button', { name: /sign out/i }).click()
  await expect(page.getByLabel(/email|이메일/i)).toBeVisible()
  await signInWithPassword(page, invited)

  const passkeyButton = page.getByRole('button', { name: /use a passkey/i })
  await expect(passkeyButton).toBeVisible({ timeout: 15_000 })
  await passkeyButton.click()
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })

  expect(consoleErrors).toEqual([])
})

test('인증기를 떼면 목록에서 사라진다', async ({ page, context, consoleErrors }) => {
  const invited = await inviteUser(page)
  await virtualAuthenticator(await context.newCDPSession(page))

  await page.goto(APP_URL)
  await signInWithPassword(page, invited)
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })

  await page.goto(`${APP_URL}/settings/passkeys`)
  await page.getByRole('button', { name: /add a passkey/i }).click()
  const rows = page.getByRole('list', { name: /two-factor/i }).getByRole('listitem')
  await expect(rows).toHaveCount(1, { timeout: 20_000 })

  // 하나뿐이면 말해 준다 — 떼면 다음 로그인에서 다시 등록하게 된다.
  await expect(page.getByRole('alert')).toContainText(/only second factor/i)

  // 버튼 이름에는 무엇을 떼는지가 붙는다(스크린리더가 읽을 것이므로).
  await page.getByRole('button', { name: /^remove /i }).click()
  await expect(rows).toHaveCount(0)
  await expect(page.getByText(/nothing registered/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
