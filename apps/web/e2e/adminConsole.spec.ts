/**
 * 관리 콘솔 — 사람과 그룹.
 *
 * 서버 테스트로는 규칙까지만 본다. **브라우저에서만 드러나는 것**이 이
 * 화면의 요지다: step-up 이 걸린 버튼을 2FA 없는 관리자가 눌렀을 때 화면이
 * 거절을 보여 주는가, 그리고 잠근 계정을 되살릴 손잡이가 실제로 있는가.
 *
 * 잠그기만 있고 되살리기가 없으면 실수 한 번이 되돌릴 수 없는 상태가 된다 —
 * 그게 이 제품에서 반복해 부딪힌 종류의 결함이다(IdP 끄기, SAML 인증서 회전).
 */

import { API, expect, inviteUser, signIn, signInWithMfa, stepUpToken, test } from './fixtures'

/** 초대 메일을 기다린다. */
test.slow()

test.describe('사람', () => {
  test('2FA 없는 관리자에게는 거절을 그대로 보여 준다', async ({ page }) => {
    const person = await inviteUser(page)

    // 시드 관리자는 2FA 가 없다. 정지는 step-up 대상이라 서버가 거절한다.
    await signIn(page)
    await page.goto('/settings/people')
    await page.getByLabel(/search by name or email/i).fill(person.email)

    const row = page.getByRole('listitem').filter({ hasText: person.email })
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: new RegExp(`^suspend ${person.displayName}$`, 'i') }).click()

    // 버튼을 숨기지 않는다. 눌러서 왜 안 되는지 읽히는 편이 정직하다.
    await expect(page.getByRole('alert').first()).toContainText(/two-factor|2단계/i)
    // 그리고 아무 일도 일어나지 않았다.
    await expect(row).not.toContainText(/suspended/i)
  })

  test('잠그고 되살린다 — 되돌릴 수 있어야 한다', async ({ page }) => {
    const person = await inviteUser(page)

    await signInWithMfa(page)
    await page.goto('/settings/people')
    await page.getByLabel(/search by name or email/i).fill(person.email)

    const row = page.getByRole('listitem').filter({ hasText: person.email })
    await expect(row).toBeVisible()

    await row.getByRole('button', { name: new RegExp(`^suspend ${person.displayName}$`, 'i') }).click()
    await expect(row).toContainText(/suspended/i)

    // 잠긴 사람은 못 들어온다. 이게 "정지" 의 의미다.
    const refused = await page.request.post(`${API}/api/v1/auth/login`, {
      data: { email: person.email, password: person.password },
    })
    expect(refused.status()).toBe(401)

    // **되살릴 손잡이가 같은 자리에 있다.**
    await row
      .getByRole('button', { name: new RegExp(`^reactivate ${person.displayName}$`, 'i') })
      .click()
    await expect(row).not.toContainText(/suspended/i)

    const allowed = await page.request.post(`${API}/api/v1/auth/login`, {
      data: { email: person.email, password: person.password },
    })
    expect(allowed.ok(), await allowed.text()).toBe(true)
  })

  test('한 사람에게만 2FA 를 강제한다', async ({ page }) => {
    const person = await inviteUser(page)

    await signInWithMfa(page)
    await page.goto('/settings/people')
    await page.getByLabel(/search by name or email/i).fill(person.email)

    const row = page.getByRole('listitem').filter({ hasText: person.email })
    await row
      .getByRole('button', {
        name: new RegExp(`change the two-factor requirement for ${person.displayName}`, 'i'),
      })
      .click()
    await expect(row).toContainText(/2fa required/i)

    // 다음 로그인에서 등록을 요구받는다 — 조직 전체를 켜지 않았는데도.
    const signedIn = await page.request.post(`${API}/api/v1/auth/login`, {
      data: { email: person.email, password: person.password },
    })
    const body = (await signedIn.json()) as { mfa_enrollment_required: boolean }
    expect(body.mfa_enrollment_required).toBe(true)

    // 되돌린다. 강제를 걸 길만 있으면 잘못 건 것을 풀 수 없다.
    await row
      .getByRole('button', {
        name: new RegExp(`change the two-factor requirement for ${person.displayName}`, 'i'),
      })
      .click()
    await expect(row).not.toContainText(/2fa required/i)
  })
})

test.describe('그룹', () => {
  test('만들고, 사람을 넣고, 지운다', async ({ page }) => {
    const person = await inviteUser(page)
    const name = `Platform ${Date.now().toString(36).slice(-5)}`

    await signIn(page)
    await page.goto('/settings/groups')

    await page.getByRole('button', { name: /^new group$/i }).click()
    await page.getByLabel(/^group name$/i).fill(name)
    await page.getByRole('button', { name: /^create group$/i }).click()
    // 폼이 닫히면 만들기가 끝난 것이다.
    await expect(page.getByRole('button', { name: /^create group$/i })).toHaveCount(0)

    const row = page.getByRole('listitem').filter({ hasText: name })
    await expect(row).toBeVisible()
    // 빈 그룹도 목록에 보인다. 안 보이면 만들기가 실패한 줄로 읽는다.
    await expect(row).toContainText(/no members/i)

    await row.getByRole('button', { name: new RegExp(`members of ${name}`, 'i') }).click()
    // 후보를 통째로 내려 고르는 드롭다운이 아니다 — 사람이 백 명을 넘으면
    // 마지막 사람이 목록에서 빠진다(실제로 개발 DB 가 103명이 되면서 여기서
    // 멈췄다). 이제 검색해서 고른다.
    await row.getByLabel(/^add someone$/i).fill(person.email)
    await row.getByRole('button', { name: new RegExp(`\\(${person.email}\\)$`) }).click()
    await expect(row).toContainText(/1 member/i)
    await expect(row).toContainText(person.email)

    // 빼는 길도 같은 자리에 있다.
    await row
      .getByRole('button', { name: new RegExp(`remove ${person.displayName} from this group`, 'i') })
      .click()
    await expect(row).toContainText(/nobody is in this group yet/i)

    // 지우기는 두 번 눌러야 한다. 권한이 함께 사라지는 일이라 한 번은 위험하다.
    await row.getByRole('button', { name: new RegExp(`^delete the group ${name}$`, 'i') }).click()
    await expect(row).toContainText(/roles given to this group are removed/i)
    await row.getByRole('button', { name: new RegExp(`^confirm deleting the group ${name}$`, 'i') }).click()
    await expect(page.getByRole('listitem').filter({ hasText: name })).toHaveCount(0)
  })

  test('IdP 가 관리하는 그룹은 손잡이를 그리지 않고 이유를 말한다', async ({ page }) => {
    // **실제로 SSO 로 들어와 만들어진 그룹**을 본다. `source='idp'` 를 손으로
    // 심으면 동기화가 그 값을 붙인다는 것을 확인하지 못한다 — sso.spec 이
    // "화면에 그룹을 보여주는 자리가 아직 없다" 고 적어 둔 자리가 여기다.
    const idp = await ensureOidcIdp(page)

    await page.goto('/')
    const button = page.getByRole('button', {
      name: new RegExp(`continue with ${idp.name}`, 'i'),
    })
    await expect(button).toBeVisible()
    await button.click()
    // 가짜 IdP 가 `groups: ["engineering"]` 을 준다. 들어오면 그 그룹이 생긴다.
    await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })

    await signIn(page)
    await page.goto('/settings/groups')
    const row = page.getByRole('listitem').filter({ hasText: 'engineering' })
    await expect(row).toBeVisible()
    await expect(row).toContainText(/from your provider/i)
    // 사람이 실제로 들어가 있다 — 그룹 동기화가 돌았다는 증거다.
    await expect(row).not.toContainText(/no members/i)

    await row.getByRole('button', { name: /members of engineering/i }).click()
    // 넣는 손잡이가 없다. 대신 왜 없는지 적혀 있다 — 손으로 넣어도 다음
    // 로그인의 동기화가 되돌린다.
    await expect(row.getByLabel(/^add someone$/i)).toHaveCount(0)
    await expect(row).toContainText(/identity provider owns this membership/i)
    await expect(
      row.getByRole('button', { name: /remove .* from this group/i }),
    ).toHaveCount(0)
  })
})

/** 브라우저가 보는 IdP 주소. `iss` 도 이것이다. */
const IDP = process.env['E2E_IDP_URL'] ?? 'http://localhost:9099'
/** API 가 보는 주소. 컴포즈 네트워크 안이다. */
const IDP_INTERNAL = process.env['E2E_IDP_INTERNAL_URL'] ?? 'http://fake-idp:9099'

/**
 * 가짜 OIDC IdP 를 등록된·켜진 상태로 만든다.
 *
 * 이름을 매번 새로 만들지 않는다: 같은 (발급자, 클라이언트) 는 하나뿐이고
 * 가짜 IdP 의 `iss`·`aud` 는 고정이다. 이미 있으면 그것을 다시 켠다 —
 * 같은 DB 로 두 번 돌려도, sso.spec 이 먼저 돌았어도 통과해야 한다.
 *
 * 이름을 **돌려주는** 이유: 먼저 등록된 것이 다른 이름일 수 있다.
 */
async function ensureOidcIdp(
  page: import('@playwright/test').Page,
): Promise<{ id: string; name: string }> {
  const headers = { Authorization: `Bearer ${await stepUpToken(page)}` }

  const listed = await page.request.get(`${API}/api/v1/admin/sso/providers`, { headers })
  expect(listed.ok(), await listed.text()).toBe(true)
  const existing = ((await listed.json()) as { id: string; issuer: string; name: string }[]).find(
    (row) => row.issuer === IDP,
  )
  if (existing) {
    const on = await page.request.post(
      `${API}/api/v1/admin/sso/providers/${existing.id}/enable`,
      { headers },
    )
    expect(on.ok(), await on.text()).toBe(true)
    return { id: existing.id, name: existing.name }
  }

  const created = await page.request.post(`${API}/api/v1/admin/sso/providers`, {
    headers,
    data: {
      name: 'Fake IdP',
      issuer: IDP,
      client_id: 'ieum-dev',
      client_secret: 'dev-secret',
      // 브라우저가 간다.
      authorization_endpoint: `${IDP}/authorize`,
      // 서버가 간다.
      token_endpoint: `${IDP_INTERNAL}/token`,
      jwks_uri: `${IDP_INTERNAL}/jwks`,
      groups_claim: 'groups',
      email_domains: ['corp.example.com'],
    },
  })
  expect(created.ok(), await created.text()).toBe(true)
  return { id: ((await created.json()) as { id: string }).id, name: 'Fake IdP' }
}
