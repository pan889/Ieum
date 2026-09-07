/**
 * OIDC SSO 로그인 (auth.md 4절). M3 완료 조건이 여기 걸려 있다.
 *
 * 개발 스택의 가짜 IdP(`fake-idp`)를 쓴다. 브라우저는 공개 포트로,
 * API 는 컴포즈 네트워크 안에서 서비스 이름으로 같은 IdP 를 본다 —
 * `iss` 는 브라우저가 보는 주소로 하나만 쓴다(MinIO presigned 와 같은 문제).
 *
 * 서버 테스트로는 응답까지만 본다. **브라우저에서만 드러난 것이 둘 있었다**:
 * 콜백이 토큰을 저장하지 않아 교환은 성공하는데 다음 요청이 401 을 받았고,
 * SSO 요청에 `anonymous` 가 없어 세션을 만드는 요청 자체가 401 처리기를
 * 건드렸다. 둘 다 콘솔에 아무것도 안 남는다.
 *
 * 그룹 동기화와 JIT 규칙은 서버 테스트가 본다(test_oidc.py) — 화면에
 * 그룹을 보여주는 자리가 아직 없다.
 */

import type { Page } from '@playwright/test'

import { API, expect, plainAdminToken, signIn, stepUpToken, test } from './fixtures'

/** 브라우저가 보는 주소. `iss` 도 이것이다. */
const IDP = process.env['E2E_IDP_URL'] ?? 'http://localhost:9099'
/** API 가 보는 주소. 컴포즈 네트워크 안이다. */
const IDP_INTERNAL = process.env['E2E_IDP_INTERNAL_URL'] ?? 'http://fake-idp:9099'

/** 새로 등록할 때 붙일 이름. 이미 등록돼 있으면 그쪽 이름을 쓴다. */
const NAME = 'Fake IdP'

/** IdP 왕복을 두 번 돈다. */
test.slow()
test.describe.configure({ mode: 'serial' })

/**
 * 가짜 IdP 를 등록된·켜진 상태로 만들고 (id, 이름) 을 돌려준다.
 *
 * 이름을 매번 새로 만들지 **않는다**: 같은 (발급자, 클라이언트) 는 하나뿐이고
 * (유일 제약), 가짜 IdP 의 `iss`·`aud` 는 고정이다. 그래서 이미 있으면 그것을
 * 다시 켠다 — 같은 DB 로 스펙을 두 번 돌려도 통과해야 한다.
 *
 * 이름을 **돌려주는** 이유: 먼저 등록된 것이 다른 이름일 수 있다. 여기서
 * `NAME` 을 가정하면 개발 DB 의 과거 등록 하나로 스펙이 붉어진다.
 */
async function ensureIdp(page: Page): Promise<{ id: string; name: string }> {
  const token = await stepUpToken(page)
  const headers = { Authorization: `Bearer ${token}` }

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
      name: NAME,
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
  return { id: ((await created.json()) as { id: string }).id, name: NAME }
}

/** 로그인 화면의 IdP 버튼. */
function ssoButton(page: Page, name: string) {
  return page.getByRole('button', { name: new RegExp(`continue with ${name}`, 'i') })
}

test('IdP 설정은 2FA 없이 열 수 없다', async ({ page, consoleErrors }) => {
  // 시드 관리자는 권한은 다 갖고 있지만 인증기가 없다. 이 설정을 쥐면
  // 발급자와 JWKS 를 바꿔 **누구로든** 로그인할 수 있으므로 step-up 대상이다.
  await signIn(page)
  await page.goto('/settings/sso')

  // 거절을 화면에 그대로 보여 준다. 숨기면 왜 안 되는지 알 수 없다.
  await expect(page.getByRole('alert')).toContainText(/two-factor/i)

  // 서버가 낸 403 이 그 거절이다. 이것 하나만 예상한다. 호스트는 굳이 맞추지
  // 않는다 — 브라우저가 보는 API 주소와 테스트가 쓰는 주소가 다르다.
  expect(consoleErrors).toEqual([
    expect.stringMatching(/^403 \S+\/api\/v1\/admin\/sso\/providers$/),
  ])
})

test('IdP 로 로그인하면 계정이 만들어진다', async ({ page, consoleErrors }) => {
  const idp = await ensureIdp(page)

  await page.goto('/')
  // 버튼이 뜬다 — 익명으로 부르는 목록이 살아 있다는 뜻이다.
  const button = ssoButton(page, idp.name)
  await expect(button).toBeVisible()
  await button.click()

  // IdP 로 갔다가 콜백을 거쳐 앱 안으로 들어온다. 여기까지 오면 콜백이 토큰을
  // 저장했다는 뜻이다 — 안 하면 다음 요청이 401 을 받고 로그인 화면으로
  // 되돌아간다(교환은 성공한 채로).
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 20_000 })
  // IdP 가 준 이름으로 계정이 생겼다(JIT 프로비저닝).
  await expect(page.getByText('SSO Person')).toBeVisible()
  // 인가 코드가 주소창에 남으면 기록·리퍼러로 샌다.
  expect(page.url()).not.toContain('code=')

  expect(consoleErrors).toEqual([])
})

test('IdP 는 껐다 켤 수 있다', async ({ page, consoleErrors }) => {
  const idp = await ensureIdp(page)
  const button = ssoButton(page, idp.name)

  await page.goto('/')
  await expect(button).toBeVisible()

  // 지우지 않고 끈다. 지우면 `user_identity` 가 따라 사라져 다시 켤 때
  // 모두가 새 계정으로 들어온다.
  const off = await page.request.post(
    `${API}/api/v1/admin/sso/providers/${idp.id}/disable`,
    { headers: { Authorization: `Bearer ${await stepUpToken(page)}` } },
  )
  expect(off.ok(), await off.text()).toBe(true)
  await page.reload()
  await expect(button).toHaveCount(0)

  // **되돌아와야 한다.** 끄기만 되면 일방통행이다 — 같은 발급자로 새로
  // 등록하는 길은 유일 제약이 막으므로 그 IdP 를 영구히 잃는다.
  const on = await page.request.post(
    `${API}/api/v1/admin/sso/providers/${idp.id}/enable`,
    { headers: { Authorization: `Bearer ${await stepUpToken(page)}` } },
  )
  expect(on.ok(), await on.text()).toBe(true)
  await page.reload()
  await expect(button).toBeVisible()

  expect(consoleErrors).toEqual([])
})

/** IdP 가 주는 메타데이터. 여기서 발급자·SSO 주소·서명 인증서가 온다. */
const SAML_METADATA = `${IDP}/saml/metadata`
const SAML_NAME = 'Fake SAML'

/**
 * 가짜 IdP 를 SAML 로 등록된·켜진 상태로 만들고 (id, 이름) 을 돌려준다.
 *
 * **메타데이터를 매번 다시 읽어 갈아 끼운다.** 가짜 IdP 는 기동할 때마다 서명
 * 키를 새로 만들므로, 컨테이너가 한 번 재시작하면 등록해 둔 인증서로는 어떤
 * 어설션도 검증되지 않는다 — 실제 IdP 의 키 회전과 같은 상황이고, 그 길이
 * 막혀 있으면 스펙이 "서명 검증 실패" 로 붉어진다.
 */
async function ensureSamlIdp(page: Page): Promise<{ id: string; name: string }> {
  const metadata = await (await page.request.get(SAML_METADATA)).text()
  const token = await stepUpToken(page)
  const headers = { Authorization: `Bearer ${token}` }

  const listed = await page.request.get(`${API}/api/v1/admin/sso/providers`, { headers })
  expect(listed.ok(), await listed.text()).toBe(true)
  const existing = (
    (await listed.json()) as { id: string; name: string; kind: string }[]
  ).find((row) => row.kind === 'saml')

  if (existing) {
    const rotated = await page.request.patch(
      `${API}/api/v1/admin/sso/saml/providers/${existing.id}`,
      { headers, data: { metadata_xml: metadata } },
    )
    expect(rotated.ok(), await rotated.text()).toBe(true)
    const on = await page.request.post(
      `${API}/api/v1/admin/sso/providers/${existing.id}/enable`,
      { headers },
    )
    expect(on.ok(), await on.text()).toBe(true)
    return { id: existing.id, name: existing.name }
  }

  const created = await page.request.post(`${API}/api/v1/admin/sso/saml/providers`, {
    headers,
    data: {
      name: SAML_NAME,
      metadata_xml: metadata,
      groups_attribute: 'groups',
      email_domains: ['corp.example.com'],
    },
  })
  expect(created.ok(), await created.text()).toBe(true)
  return { id: ((await created.json()) as { id: string }).id, name: SAML_NAME }
}

test('SAML IdP 로 로그인하면 계정이 만들어진다', async ({ page, consoleErrors }) => {
  const idp = await ensureSamlIdp(page)

  await page.goto('/')
  const button = ssoButton(page, idp.name)
  await expect(button).toBeVisible()
  await button.click()

  // IdP 가 폼을 스스로 보내 우리 ACS 로 POST 하고, ACS 는 브라우저를 화면으로
  // 되돌린다. 화면은 1회용 코드를 토큰으로 바꾼다 — **이 왕복은 서버 테스트로
  // 볼 수 없다**: 브라우저가 폼을 보내고 리다이렉트를 따라가야 성립한다.
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible({ timeout: 25_000 })
  await expect(page.getByText('SAML Person')).toBeVisible()
  // 1회용 코드가 주소창에 남으면 기록·리퍼러로 샌다.
  expect(page.url()).not.toContain('code=')

  expect(consoleErrors).toEqual([])
})

test('SAML 설정도 2FA 없이 만질 수 없다', async ({ page, consoleErrors }) => {
  // 인증서를 바꾸면 **누구로든 로그인할 수 있다.** 등록과 같은 문이어야 한다.
  const idp = await ensureSamlIdp(page)
  await signIn(page)

  const refused = await page.request.patch(
    `${API}/api/v1/admin/sso/saml/providers/${idp.id}`,
    {
      headers: { Authorization: `Bearer ${await plainAdminToken(page)}` },
      data: { certificates: ['AAAA'] },
    },
  )
  expect(refused.status()).toBe(403)

  expect(consoleErrors).toEqual([])
})
