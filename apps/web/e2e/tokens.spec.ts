import { expect, signIn, test } from './fixtures'

test('2FA 없이는 토큰을 만들 수 없다', async ({ page }) => {
  // 시드 관리자는 2FA 를 등록하지 않은 상태다. PAT 은 만료 없는 무기명
  // 자격증명이라 비밀번호 하나로 발급되면 안 된다.
  await signIn(page)
  await page.goto('/settings/tokens')

  await page.getByRole('button', { name: /new token/i }).click()
  await page.getByLabel(/^name$/i).fill('ci')
  await page.getByRole('button', { name: 'identity.user.view' }).click()
  await page.getByRole('button', { name: /create token/i }).click()

  await expect(page.getByRole('alert')).toContainText(/two-factor/i)
})

test('토큰 목록은 비어 있을 때 그렇게 말한다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/tokens')
  await expect(page.getByText(/no tokens yet/i)).toBeVisible()
  expect(consoleErrors).toEqual([])
})

test('권한 목록을 스코프 후보로 보여준다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/settings/tokens')
  await page.getByRole('button', { name: /new token/i }).click()

  // 서버가 등록한 권한 상수를 그대로 쓴다 — 프론트에 하드코딩하면 갈라진다.
  await expect(page.getByRole('button', { name: 'issue.view' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'identity.token.issue' })).toBeVisible()

  const chip = page.getByRole('button', { name: 'issue.view' })
  await expect(chip).toHaveAttribute('aria-pressed', 'false')
  await chip.click()
  await expect(chip).toHaveAttribute('aria-pressed', 'true')

  expect(consoleErrors).toEqual([])
})
