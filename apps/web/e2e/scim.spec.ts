/**
 * SCIM 프로비저닝 (M4).
 *
 * 서버 시험이 규약과 자물쇠를 이미 붙잡고 있다. 브라우저로 한 번 더 보는
 * 이유는 하나다: **관리 화면에서 받은 토큰이 실제로 SCIM 문을 여는가.**
 *
 * 두 쪽이 따로 맞아도 사이가 끊겨 있으면 아무도 모른다 — 서버 시험은 자기가
 * 만든 토큰을 쓰고, 화면 시험은 글자가 떴는지만 본다. 그 사이를 잇는 것은
 * 여기뿐이다.
 *
 * 그리고 **토큰은 한 번만 보인다.** 다시 볼 수 없다는 것이 화면의 약속이고,
 * 재발급이 옛 토큰을 죽인다는 것이 그 약속을 지키는 방법이다.
 */

import { API, expect, signInWithMfa, stepUpToken, test } from './fixtures'

test.slow()

const SCIM_ISSUER = 'https://scim-e2e.example'

test.describe('SCIM 프로비저닝', () => {
  test('화면에서 받은 토큰으로 IdP 가 계정을 밀어 넣는다', async ({ page }) => {
    // 프로비저닝을 붙일 IdP 하나. 화면에서 만들려면 SAML 메타데이터가
    // 필요하고, 그건 이 스펙이 보려는 것이 아니다.
    const token = await stepUpToken(page)
    const headers = { Authorization: `Bearer ${token}` }
    const listed = await page.request.get(`${API}/api/v1/admin/sso/providers`, { headers })
    const rows = (await listed.json()) as { id: string; issuer: string; name: string }[]
    const existing = rows.find((row) => row.issuer === SCIM_ISSUER)
    const name = existing?.name ?? `SCIM E2E ${String(Date.now()).slice(-5)}`
    if (existing) {
      const on = await page.request.post(
        `${API}/api/v1/admin/sso/providers/${existing.id}/enable`,
        { headers },
      )
      expect(on.ok(), await on.text()).toBe(true)
    } else {
      const created = await page.request.post(`${API}/api/v1/admin/sso/providers`, {
        headers,
        data: {
          name,
          issuer: SCIM_ISSUER,
          client_id: 'scim-e2e',
          client_secret: 'dev-secret',
          authorization_endpoint: `${SCIM_ISSUER}/authorize`,
          token_endpoint: `${SCIM_ISSUER}/token`,
          jwks_uri: `${SCIM_ISSUER}/jwks`,
        },
      })
      expect(created.ok(), await created.text()).toBe(true)
    }

    await signInWithMfa(page)
    await page.goto('/settings/sso')
    const row = page.locator('li').filter({ hasText: name })
    await expect(row).toBeVisible()

    // 아직 한 번도 안 왔다고 말한다. 안 보이면 프로비저닝이 도는지 확인할
    // 방법이 없다.
    await expect(row.getByText(/nothing has arrived yet/i)).toBeVisible()

    await row.getByRole('button', { name: /^issue token$/i }).click()
    await expect(row.getByText(/shown once/i)).toBeVisible()
    const issued = (await row.locator('code').innerText()).trim()
    expect(issued.length).toBeGreaterThan(20)

    // **이제 IdP 가 된다.** 화면이 준 글자 그대로 SCIM 문을 두드린다.
    const email = `scim-e2e-${String(Date.now()).slice(-8)}@example.com`
    const scim = { Authorization: `Bearer ${issued}` }
    const before = await page.request.get(
      `${API}/scim/v2/Users?filter=${encodeURIComponent(`userName eq "${email}"`)}`,
      { headers: scim },
    )
    expect(before.status(), await before.text()).toBe(200)
    expect(((await before.json()) as { totalResults: number }).totalResults).toBe(0)

    const created = await page.request.post(`${API}/scim/v2/Users`, {
      headers: scim,
      data: {
        schemas: ['urn:ietf:params:scim:schemas:core:2.0:User'],
        userName: email,
        displayName: 'SCIM 이 만든 사람',
      },
    })
    expect(created.status(), await created.text()).toBe(201)
    const userId = ((await created.json()) as { id: string }).id

    // 관리 화면의 사람 목록에 실제로 나타난다. 이게 "밀어 넣었다" 의 뜻이다.
    await page.goto('/settings/people')
    await page.getByRole('textbox', { name: /search|찾기/i }).fill(email)
    await expect(page.getByText(email)).toBeVisible()

    // 재발급하면 **옛 토큰이 죽는다.** 유출된 토큰을 끊는 유일한 방법이다.
    await page.goto('/settings/sso')
    const again = page.locator('li').filter({ hasText: name })
    await again.getByRole('button', { name: /^reissue token$/i }).click()
    await expect(again.getByText(/shown once/i)).toBeVisible()

    const dead = await page.request.get(`${API}/scim/v2/Users/${userId}`, { headers: scim })
    expect(dead.status()).toBe(401)
    expect(dead.headers()['content-type']).toContain('application/scim+json')
  })

  test('2FA 를 통과하지 않으면 토큰을 못 받는다', async ({ page }) => {
    // 이 토큰을 쥐면 계정을 만들고 끌 수 있다 — IdP 설정과 같은 무게다.
    const { plainAdminToken } = await import('./fixtures')
    const listed = await page.request.get(`${API}/api/v1/admin/sso/providers`, {
      headers: { Authorization: `Bearer ${await stepUpToken(page)}` },
    })
    const first = ((await listed.json()) as { id: string }[])[0]
    expect(first, '스펙보다 먼저 IdP 가 하나 있어야 한다').toBeTruthy()

    const refused = await page.request.post(
      `${API}/api/v1/admin/sso/providers/${(first as { id: string }).id}/scim-token`,
      { headers: { Authorization: `Bearer ${await plainAdminToken(page)}` } },
    )
    expect(refused.status()).toBe(403)
  })
})
