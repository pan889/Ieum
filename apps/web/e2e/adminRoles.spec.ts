/**
 * 역할과 권한 화면.
 *
 * 서버 테스트로는 규칙까지만 본다. 브라우저에서만 드러나는 것: step-up 이
 * 걸린 화면이 거절을 **보여 주는가**, 준 것을 **되돌릴 손잡이가 있는가**,
 * 그리고 고칠 수 없는 자리에 왜 그런지 적혀 있는가.
 *
 * 이 화면이 없던 동안 역할은 만들고 줄 수만 있었다. 잘못 만든 역할은 같은
 * 이름으로 다시 만들 수 없고(유일 제약), 잘못 준 할당은 영구히 남았다.
 */

import { expect, inviteUser, signIn, signInWithMfa, test, uniqueKey } from './fixtures'

/** 초대 메일을 기다린다. */
test.slow()

test('2FA 없는 관리자에게는 목록조차 주지 않고, 그 거절을 보여 준다', async ({ page }) => {
  // 시드 관리자는 권한은 다 갖고 있지만 인증기가 없다. 권한 스킴을 쥐면
  // 자기에게 무엇이든 줄 수 있으므로 step-up 대상이다(auth.md 3절).
  await signIn(page)
  await page.goto('/settings/roles')

  await expect(page.getByRole('alert').first()).toContainText(/two-factor|2단계/i)
})

test('역할을 만들고, 권한을 고르고, 주고, 되돌린다', async ({ page }) => {
  const person = await inviteUser(page)
  const name = `Auditor ${uniqueKey('R')}`

  await signInWithMfa(page)
  await page.goto('/settings/roles')

  await page.getByRole('button', { name: /^new role$/i }).click()
  await page.getByLabel(/^role name$/i).fill(name)
  await page.getByRole('button', { name: /^create role$/i }).click()
  // 폼이 닫히면 만들기가 끝난 것이다.
  await expect(page.getByRole('button', { name: /^create role$/i })).toHaveCount(0)

  const row = page.getByRole('listitem').filter({ hasText: name })
  await expect(row).toBeVisible()
  // 권한 없이 태어난다. 만들기 폼에 목록 서른 개를 펼쳐 두지 않기 때문이다.
  await expect(row).toContainText(/no permissions|0 permissions/i)

  await row.getByRole('button', { name: new RegExp(`^edit ${name}$`, 'i') }).click()

  // 권한을 하나 켠다. 켜자마자 저장된다 — "저장" 버튼을 따로 두면 눌렀는지
  // 아닌지가 화면에 남고, 안 누른 사람은 권한을 준 줄로 안다.
  const chip = row.getByRole('button', { name: /\(identity\.audit\.view\)$/ })
  await expect(chip).toBeVisible()
  await chip.click()
  await expect(chip).toHaveAttribute('aria-pressed', 'true')
  await expect(row).toContainText(/1 permission/i)

  // 사람에게 준다.
  await row.getByLabel(/^give to$/i).selectOption({
    label: `${person.displayName} (${person.email})`,
  })
  await row.getByRole('button', { name: /^add$/i }).click()
  await expect(row).toContainText(person.email)
  await expect(row).toContainText(/given to 1 principal/i)

  // **되돌린다.** 주는 길만 있으면 그건 권한 관리가 아니다.
  await row
    .getByRole('button', {
      name: new RegExp(`take this role back from ${person.email}`, 'i'),
    })
    .click()
  await expect(row).toContainText(/nobody has this role yet/i)

  // 역할도 지운다. 두 번 눌러야 한다 — 받은 사람이 권한을 잃는 일이다.
  await row.getByRole('button', { name: new RegExp(`^delete the role ${name}$`, 'i') }).click()
  await expect(row).toContainText(/loses the permissions/i)
  await row
    .getByRole('button', { name: new RegExp(`^confirm deleting the role ${name}$`, 'i') })
    .click()
  await expect(page.getByRole('listitem').filter({ hasText: name })).toHaveCount(0)
})

test('내장 역할은 권한 손잡이를 그리지 않고 이유를 말한다', async ({ page }) => {
  await signInWithMfa(page)
  await page.goto('/settings/roles')

  const row = page.getByRole('listitem').filter({ hasText: 'Administrator' })
  await expect(row).toBeVisible()
  await expect(row).toContainText(/built-in/i)
  // 지우는 손잡이가 없다 — 시드가 다음 기동에 다시 만든다.
  await expect(
    row.getByRole('button', { name: /^delete the role Administrator$/i }),
  ).toHaveCount(0)

  await row.getByRole('button', { name: /^edit Administrator$/i }).click()
  await expect(row).toContainText(/restored from their definition on every deploy/i)
  // 권한 칩이 없다.
  await expect(row.getByRole('button', { name: /\(identity\.audit\.view\)$/ })).toHaveCount(0)
})

test('자기 관리자 역할은 스스로 회수할 수 없다', async ({ page }) => {
  // 마지막 관리자가 자기 할당을 회수하면 되돌려 줄 사람이 없다.
  await signInWithMfa(page)
  await page.goto('/settings/roles')

  const row = page.getByRole('listitem').filter({ hasText: 'Administrator' })
  await row.getByRole('button', { name: /^edit Administrator$/i }).click()

  const mine = row.getByRole('button', {
    name: /take this role back from mfa-admin@example\.com/i,
  })
  await expect(mine).toBeVisible()
  await mine.click()

  // 거절이 화면에 뜬다. 버튼을 숨기지 않는다 — 남의 관리자 자리는 거둘 수
  // 있어야 하고(퇴사 처리), 자기 것만 막힌다.
  await expect(row.getByRole('alert').first()).toContainText(/own administrator role/i)
  await expect(row).toContainText('mfa-admin@example.com')
})
