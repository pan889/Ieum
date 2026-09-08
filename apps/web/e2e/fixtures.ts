import { createHmac } from 'node:crypto'

import { expect, test as base } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

export const ADMIN_EMAIL = process.env['SEED_ADMIN_EMAIL'] ?? 'admin@example.com'
export const ADMIN_PASSWORD = process.env['SEED_ADMIN_PASSWORD'] ?? 'seed-admin-password-1234'

/**
 * 테스트마다 자기 것을 만든다. 시드 DB 를 공유해도 서로 안 밟는다.
 *
 * 무작위 네 글자로는 모자란다. 같은 DB 로 수십 번 돌리는 사이 지난 실행이
 * 남긴 것이 수백 개로 쌓이고, 그러면 생일 문제로 이따금 "이미 쓰는 키" 가
 * 뜬다 — 코드가 멀쩡한데 테스트만 붉어진다. 시각을 섞어 실행끼리 갈라 두고,
 * 무작위는 같은 밀리초 안을 가른다.
 */
let sequence = 0

export function uniqueKey(prefix: string): string {
  sequence += 1
  const when = Date.now().toString(36).slice(-5).toUpperCase()
  // 한 실행 안에서는 이 번호가 겹치지 않는다. 시각만으로는 같은 밀리초에
  // 만든 둘이 같아진다.
  const seq = sequence.toString(36).toUpperCase().padStart(2, '0')
  const salt = Math.random().toString(36).slice(2, 4).padEnd(2, '0').toUpperCase()
  return `${prefix}${when}${seq}${salt}`
}

/** 테스트마다 자기 프로젝트를 만든다. */
export function projectKey(): string {
  return uniqueKey('E')
}

export const API = process.env['E2E_API_BASE_URL'] ?? 'http://127.0.0.1:8000'
/** 개발 스택의 메일 상자. 나간 메일을 여기서 되읽는다. */
export const MAILPIT = process.env['E2E_MAILPIT_URL'] ?? 'http://127.0.0.1:8025'

/** 아웃박스는 15초마다 훑는다. 메일은 그 뒤에 나간다. */
const SWEEP_MS = 15_000

export interface InvitedUser {
  email: string
  password: string
  displayName: string
}

/**
 * 두 번째 사람을 만든다 — **초대 메일을 실제로 읽어서**.
 *
 * 알림이 남에게 가는지, 권한이 남에게 어떻게 보이는지는 사람이 둘이라야
 * 볼 수 있다. 토큰을 몰래 만들어 지름길로 가지 않는다: 그러면 초대 흐름이
 * 죽어 있어도 테스트는 통과한다 (실제로 한동안 죽어 있었다).
 *
 * 메일을 기다리므로 느리다. 쓰는 테스트는 `test.slow()` 를 붙인다.
 */
export async function inviteUser(
  page: Page,
  options: { grants?: string[]; isCustomer?: boolean } = {},
): Promise<InvitedUser> {
  const stamp = uniqueKey('U').toLowerCase()
  const user: InvitedUser = {
    email: `${stamp}@example.com`,
    password: `${stamp}-password-1234`,
    displayName: `User ${stamp}`,
  }

  const token = await accessToken(page)
  const created = await page.request.post(`${API}/api/v1/users/invite`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      email: user.email,
      display_name: user.displayName,
      locale: 'en',
      // 고객 계정은 **만들 때만** 정한다. 나중에 뒤집는 길이 없다.
      ...(options.isCustomer ? { is_customer: true } : {}),
    },
  })
  expect(created.ok(), await created.text()).toBe(true)
  const { id } = (await created.json()) as { id: string }

  if (options.grants?.length) {
    // 초대받은 사람에게는 아무 권한도 없다. 볼 것을 주지 않으면 화면이 전부
    // "권한이 없습니다" 다.
    const role = await page.request.post(`${API}/api/v1/roles`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: `Role ${stamp}`, scope_kind: 'global', grants: options.grants },
    })
    expect(role.ok(), await role.text()).toBe(true)
    const assigned = await page.request.post(`${API}/api/v1/roles/assignments`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        role_id: ((await role.json()) as { id: string }).id,
        scope_kind: 'global',
        scope_id: null,
        principal_kind: 'user',
        principal_id: id,
      },
    })
    expect(assigned.ok(), await assigned.text()).toBe(true)
  }

  await acceptInvite(page, user)
  return user
}

/** 메일함에서 초대 링크를 찾아 열고 비밀번호를 정한다. */
/**
 * 관리자로 포털과 요청 폼을 하나 만든다. API 로 만드는 것이 맞다 —
 * 화면으로 만들면 포털 스펙마다 관리 화면을 다시 거치게 되고, 관리 화면이
 * 깨졌을 때 고객 쪽 스펙까지 함께 붉어져 원인이 흐려진다.
 */
export interface PortalFixture {
  slug: string
  requestTypeId: string
  formLabel: string
  /** 관리 화면의 프로젝트 선택 상자에서 이 창구를 찾는 데 쓴다. */
  projectId: string
  projectKey: string
}

export async function createPortal(
  page: Page,
  options: { isPublic?: boolean } = {},
): Promise<PortalFixture> {
  const token = await accessToken(page)
  const headers = { Authorization: `Bearer ${token}` }
  const stamp = uniqueKey('P').toLowerCase()

  const key = projectKey()
  const project = await page.request.post(`${API}/api/v1/projects`, {
    headers,
    data: { key, name: `Desk ${stamp}` },
  })
  expect(project.ok(), await project.text()).toBe(true)
  const projectId = ((await project.json()) as { id: string }).id

  const types = await page.request.get(`${API}/api/v1/issues/types?project_id=${projectId}`, {
    headers,
  })
  expect(types.ok(), await types.text()).toBe(true)
  const firstType = ((await types.json()) as { id: string }[])[0]
  // 유형이 없으면 요청 폼을 만들 수 없다. 여기서 죽는 편이 낫다 — 뒤에서
  // "요청 유형을 못 찾았다" 로 나오면 원인이 흐려진다.
  expect(firstType, '프로젝트에 이슈 유형이 없다').toBeTruthy()
  const issueTypeId = (firstType as { id: string }).id

  const portal = await page.request.post(`${API}/api/v1/portals`, {
    headers,
    data: {
      project_id: projectId,
      name: `Support ${stamp}`,
      slug: stamp,
      is_public: options.isPublic ?? true,
    },
  })
  expect(portal.ok(), await portal.text()).toBe(true)
  const portalId = ((await portal.json()) as { id: string }).id

  // 라벨은 **관리자가 입력한 데이터**다. 번역되지 않는다는 것을 스펙이
  // 확인할 수 있도록, 영어 UI 에서도 눈에 띄는 글자로 둔다.
  const formLabel = 'What is broken'
  const requestType = await page.request.post(
    `${API}/api/v1/portals/${portalId}/request-types`,
    {
      headers,
      data: {
        issue_type_id: issueTypeId,
        name: `Broken thing ${stamp}`,
        form_schema: {
          fields: [
            { key: 'summary', label: formLabel, required: true },
            { key: 'description', label: 'Tell us more' },
          ],
        },
        field_mapping: {},
      },
    },
  )
  expect(requestType.ok(), await requestType.text()).toBe(true)
  return {
    slug: stamp,
    requestTypeId: ((await requestType.json()) as { id: string }).id,
    formLabel,
    projectId,
    projectKey: key,
  }
}

async function acceptInvite(page: Page, user: InvitedUser): Promise<void> {
  const link = await inviteLink(page, user.email)
  // 메일의 주소는 서버가 아는 호스트다. 테스트가 여는 호스트로 맞춘다.
  await page.goto(new URL(link).pathname + new URL(link).search)
  await page.getByLabel(/new password/i).fill(user.password)
  await page.getByRole('button', { name: /activate account/i }).click()
  await expect(page.getByRole('button', { name: /^sign in$/i })).toBeVisible()
}

async function inviteLink(page: Page, email: string): Promise<string> {
  const deadline = Date.now() + SWEEP_MS * 3
  while (Date.now() < deadline) {
    const found = await page.request.get(
      `${MAILPIT}/api/v1/search?query=${encodeURIComponent(email)}`,
    )
    const messages = ((await found.json()) as { messages?: { ID: string }[] }).messages ?? []
    const first = messages[0]
    if (first) {
      const detail = await page.request.get(`${MAILPIT}/api/v1/message/${first.ID}`)
      const body = ((await detail.json()) as { Text?: string }).Text ?? ''
      const link = /https?:\/\/\S+\/invite\?token=\S+/.exec(body)?.[0]
      if (link) return link
    }
    await page.waitForTimeout(2000)
  }
  throw new Error(`초대 메일이 오지 않았다: ${email}`)
}

const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'

/**
 * TOTP 코드 (RFC 6238: 30초·6자리·SHA1 — 서버가 쓰는 그대로).
 *
 * 라이브러리를 하나 더 들이지 않는다. 이 열 줄이 인증기 앱 한 대다.
 */
export function totpCode(secret: string, at: number = Date.now()): string {
  let bits = ''
  for (const char of secret.replace(/=+$/, '').toUpperCase()) {
    const index = BASE32.indexOf(char)
    if (index < 0) throw new Error(`base32 가 아니다: ${secret}`)
    bits += index.toString(2).padStart(5, '0')
  }
  const bytes = Buffer.from((bits.match(/.{8}/g) ?? []).map((byte) => parseInt(byte, 2)))
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)))
  const digest = createHmac('sha1', bytes).update(counter).digest()
  // RFC 6238 의 동적 절단. 마지막 바이트 하위 4비트가 읽을 자리를 가리킨다.
  const offset = (digest[digest.length - 1] as number) & 0x0f
  return String((digest.readUInt32BE(offset) & 0x7fff_ffff) % 1_000_000).padStart(6, '0')
}

/**
 * 2FA 를 갖춘 관리자. **시드가 만든다** — 시크릿을 환경이 정해 주기 때문에
 * 테스트가 코드를 계산할 수 있는 유일한 계정이다 (seed.py `_seed_mfa_admin`).
 *
 * 시드 관리자(`ADMIN_EMAIL`)에 인증기를 붙이지 않는 이유: 그 계정에 하나라도
 * 생기면 로그인마다 코드를 요구받아 모든 스펙의 로그인 경로가 바뀐다.
 */
export const MFA_ADMIN = {
  email: process.env['SEED_MFA_ADMIN_EMAIL'] ?? 'mfa-admin@example.com',
  password: process.env['SEED_MFA_ADMIN_PASSWORD'] ?? 'seed-mfa-admin-password-1234',
}
const MFA_ADMIN_SECRET = process.env['SEED_MFA_ADMIN_TOTP_SECRET'] ?? 'IEUMDEVSEEDTOTPSECRET234'

/**
 * 이미 쓴 타임스텝. 서버는 **맞은 코드의 스텝 이하**를 다시 받지 않으므로
 * (재사용 방지), 같은 30초 안에 두 번 증명하려면 다음 스텝을 기다려야 한다.
 */
let usedStep = -1

/**
 * 스텝이 끝나기 직전에는 코드를 내주지 않는다. 받아서 여섯 자를 타이핑하고
 * 보내는 사이에 스텝이 넘어가면 서버가 거절하고, 픽스처는 다시 시도하지
 * 않아 스펙이 붉어진다 — 실제로 그렇게 붉어졌고, 원인이 코드가 아니라
 * **경계**여서 한참 걸렸다.
 */
const TYPING_MARGIN_MS = 6000

async function freshCode(page: Page): Promise<string> {
  for (;;) {
    const now = Date.now()
    const step = Math.floor(now / 30_000)
    const remaining = (step + 1) * 30_000 - now
    if (usedStep < step && remaining > TYPING_MARGIN_MS) {
      usedStep = step
      return totpCode(MFA_ADMIN_SECRET, now)
    }
    await page.waitForTimeout(1000)
  }
}

/**
 * 앱의 정본 주소.
 *
 * WebAuthn 자격증명은 **호스트에 묶인다**(rp_id). 서버는 `IEUM_BASE_URL` 의
 * 호스트를 rp_id 로 쓰므로, 패스키를 다루는 스펙은 그 주소로 열어야 한다 —
 * `127.0.0.1` 로 열면 `localhost` 에 등록한 키가 아예 보이지 않는다. 나머지
 * 스펙은 어느 쪽이든 상관없어서 기본 baseURL 을 건드리지 않는다.
 */
export const APP_URL = process.env['E2E_APP_URL'] ?? 'http://localhost:5173'

/** 서버의 step-up 창은 5분이다. 그 안쪽으로 넉넉히 잡아 다시 쓴다. */
const STEP_UP_REUSE_MS = 4 * 60_000
let cached: { token: string; at: number } | null = null

/**
 * step-up 을 통과할 수 있는 액세스 토큰.
 *
 * step-up 이 필요한 설정(역할·IdP·워크플로우·웹훅)은 **실제로 2FA 를 통과한**
 * 세션만 받는다 — 비밀번호만 통과한 세션은 거절한다(auth.md 6절).
 *
 * 아직 창 안이면 같은 토큰을 다시 준다. 매번 새로 만들면 코드 재사용 방지
 * 때문에 30초씩 기다리게 되고, 한 스펙이 분 단위로 늘어난다. 창을 넘기면
 * 새로 만든다 — 한 번 얻은 것을 스펙 전체에서 돌려쓰면 느린 실행에서만
 * 터지는 테스트가 된다.
 */
export async function stepUpToken(page: Page): Promise<string> {
  if (cached && Date.now() - cached.at < STEP_UP_REUSE_MS) return cached.token

  const signedIn = await page.request.post(`${API}/api/v1/auth/login`, { data: MFA_ADMIN })
  expect(signedIn.ok(), await signedIn.text()).toBe(true)
  const token = ((await signedIn.json()) as { access_token: string }).access_token

  // 로그인만으로는 "미완료" 세션이다. 여기서 증명해야 step-up 이 열린다.
  const verified = await page.request.post(`${API}/api/v1/auth/mfa/verify`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { code: await freshCode(page) },
  })
  expect(verified.ok(), await verified.text()).toBe(true)
  cached = { token, at: Date.now() }
  return token
}

/**
 * 시드 관리자의 액세스 토큰 — **2FA 를 통과하지 않은 것.**
 *
 * step-up 이 걸린 자리가 이것을 거절하는지 볼 때 쓴다. `stepUpToken` 과 짝이다.
 */
export async function plainAdminToken(page: Page): Promise<string> {
  return accessToken(page)
}

/** 로그인한 사람의 액세스 토큰. 메모리에만 있어서 앱에게 물어야 한다. */
async function accessToken(page: Page): Promise<string> {
  const response = await page.request.post(`${API}/api/v1/auth/login`, {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  })
  expect(response.ok(), await response.text()).toBe(true)
  return ((await response.json()) as { access_token: string }).access_token
}

export async function signIn(
  page: Page,
  who: { email: string; password: string } = { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
): Promise<void> {
  await page.goto('/')
  // 화면 언어는 서버가 기억한다. 앞선 테스트가 한국어로 바꿔 두었으면 로그인
  // 직후 UI 가 한국어다 — 영어 글자만 찾으면 거기서 멈춘다.
  const email = page.getByLabel(/email|이메일/i)
  const out = page.getByRole('button', { name: /sign out|로그아웃/i })

  // 한 브라우저에서 사람을 바꿔 가며 보는 테스트가 있다. 이미 들어와 있으면
  // 로그인 폼이 아예 없으므로 먼저 나간다.
  await expect(email.or(out).first()).toBeVisible()
  if (await out.isVisible()) {
    await out.click()
    await expect(email).toBeVisible()
  }

  await email.fill(who.email)
  await page.getByLabel(/password|비밀번호/i).fill(who.password)
  await page.getByRole('button', { name: /^(sign in|로그인)$/i }).click()
  // 주소가 아니라 **앱 셸**이 뜰 때까지 기다린다. 나갔다 들어오는 경우 주소는
  // 이미 `/projects` 라서, 주소만 보면 로그인 요청이 아직 날아가는 중인데도
  // 다음 줄로 넘어간다.
  await expect(out).toBeVisible()
  await page.waitForURL(/\/projects/)
}

/**
 * 브라우저로 2FA 관리자를 들여보낸다 — 화면의 2단계 인증까지 통과해서.
 *
 * step-up 이 걸린 **화면**(사람·역할·워크플로우 설정)을 실제로 눌러 보려면
 * 토큰만으로는 안 된다. `stepUpToken` 은 API 를 직접 치는 자리용이다.
 *
 * 코드를 계산하는 계정이 이것뿐인 이유는 `MFA_ADMIN` 주석에 있다.
 */
/**
 * 고객으로 들어간다. `signIn` 을 못 쓴다 — 그쪽은 앱 셸(로그아웃 버튼)과
 * `/projects` 를 기다리는데, 고객은 그 화면에 **절대 도착하지 않는다.**
 * 대신 자기 창구로 넘겨진다.
 *
 * 넘겨지는 자리가 이 헬퍼의 요점이다: 이 브라우저가 포털을 한 번도 열지
 * 않았어도(기억해 둔 창구 없음) 서버의 창구 목록으로 찾아가야 한다.
 */
export async function signInAsCustomer(
  page: Page,
  who: { email: string; password: string },
): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/email|이메일/i).fill(who.email)
  await page.getByLabel(/password|비밀번호/i).fill(who.password)
  await page.getByRole('button', { name: /^(sign in|로그인)$/i }).click()

  // **주소를 기다리지 않는다.** 창구가 하나면 그리로 넘겨지지만, 여럿이면
  // 고르는 화면이 뜨고 주소는 `/` 에 남는다 — 개발 DB 에는 지난 실행이
  // 남긴 창구가 여러 개 쌓여 있으므로 후자가 보통이다. 주소로 기다리면
  // 스펙이 "창구가 하나일 때만" 통과한다.
  //
  // 대신 **고객 표면에 도착했는가**를 기다린다: 포털 헤더든 고르는 화면이든.
  await expect(
    page
      .getByText(/uses the customer portal|고객 포털을 씁니다/i)
      .or(page.getByRole('heading', { name: /what do you need help with|무엇을 도와/i }))
      .first(),
  ).toBeVisible({ timeout: 20_000 })
  // 그리고 내부 앱이 아니다. 이것이 이 헬퍼의 계약이다.
  await expect(page.getByRole('link', { name: /^projects$/i })).toHaveCount(0)
}

export async function signInWithMfa(page: Page): Promise<void> {
  // `signIn` 을 못 쓴다: 그쪽은 앱 셸(로그아웃 버튼)이 뜰 때까지 기다리는데,
  // 이 계정은 비밀번호만으로는 2단계 화면에서 멈춘다.
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

  // 로그인만으로는 "미완료" 세션이다. 앱 셸이 2단계 화면을 띄운다.
  const code = page.getByLabel(/authentication code|인증 코드/i)
  await expect(code).toBeVisible()

  // **거절당하면 다음 스텝으로 한 번 더 시도한다.**
  //
  // 서버는 재사용을 막으려고 "맞은 코드의 스텝 이하" 를 거절하고, 맞는 스텝을
  // ±1 범위에서 찾는다. 그래서 앞 시험이 방금 통과한 직후에는, 이 쪽이 새
  // 스텝의 코드를 보내도 서버가 그것을 이전 스텝으로 맞춰 버리는 경우가
  // 생긴다 — 스펙 한 파일에서 2FA 로 여러 번 들어가면 실제로 그렇게 붉어진다.
  //
  // 한 번 더 시도하면 그 창을 확실히 벗어난다. 무한히 돌지 않는 것이
  // 중요하다: 비밀이 틀린 것(진짜 결함)과 구별되어야 한다.
  const wrong = page.getByText(/code isn't right|코드가 올바르지/i)
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const digits = await freshCode(page)
    // 한 글자씩 넣고 **들어간 것을 확인한다.** 이 입력은 제어 컴포넌트라
    // 값이 상태로 들어가지 않으면 폼은 빈 코드를 보내고, 서버는 그것을 422 로
    // 거절한다 — 화면에는 "코드가 틀렸다" 도 안 뜬다. 실제로 그렇게 붉어졌고,
    // 원인을 찾는 데 오래 걸렸다. `fill` 이 아니라 타이핑으로 넣는다.
    await code.clear()
    await code.pressSequentially(digits)
    await expect(code).toHaveValue(digits)
    await page.getByRole('button', { name: /^(verify|확인)$/i }).click()
    // 앱 셸이 뜨거나, 틀렸다는 말이 뜬다. 둘 중 하나는 온다.
    await expect(out.or(wrong).first()).toBeVisible()
    if (await out.isVisible()) return
  }
  // 세 번 다 거절당했으면 스텝 문제가 아니다 — 비밀이 어긋난 것이다.
  await expect(out).toBeVisible()
}

export async function createProject(page: Page, key: string, name = 'E2E'): Promise<void> {
  await page.getByRole('link', { name: /^projects$/i }).click()
  await page.waitForURL(/\/projects$/)
  await page.getByRole('button', { name: /new project/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(name)
  await page.getByRole('button', { name: /create project/i }).click()
  // 폼이 닫히면 생성이 끝난 것이다. 끝나기 전에 검색하면 목록 질의가
  // 생성보다 먼저 끝나 버려서 방금 만든 프로젝트를 못 찾는다.
  await expect(page.getByRole('button', { name: /create project/i })).toHaveCount(0)
  // 목록의 첫 페이지에 있으리라 기대하지 않는다. 프로젝트는 키 순이라
  // 새로 만든 게 몇 페이지 뒤에 있을 수 있다 — 검색으로 확인한다.
  await page.getByLabel(/find a project/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
  await page.getByLabel(/find a project/i).fill('')
}

/** 위키 스페이스를 만든다. 여섯 개 스펙이 같은 것을 여섯 번 적어 두고 있었다. */
export async function createSpace(page: Page, key: string): Promise<void> {
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  // 폼이 닫히면 생성이 끝난 것이다. 끝나기 전에 검색하면 목록 질의가
  // 생성보다 먼저 끝나 버려서 방금 만든 스페이스를 못 찾는다.
  await expect(page.getByRole('button', { name: /create space/i })).toHaveCount(0)
  await page.getByLabel(/find a space/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
}

/**
 * 프로젝트 피커에서 프로젝트를 고른다.
 *
 * 이슈 생성·목록 필터·보드가 전부 `<select>` 를 버리고 **검색으로** 고르게
 * 바뀌었다. 목록을 통째로 받아 그리면 개수가 상한을 넘는 순간 방금 만든
 * 프로젝트가 옵션에서 사라지기 때문이다 — 개발 DB 가 2334개가 되자
 * `boards.spec.ts` 와 `bulk.spec.ts` 가 실제로 그렇게 붉어졌다
 * (`allProjects.ts`, ux-principles 4절).
 *
 * 피커는 **고른 뒤 접힌다.** 접힌 상태에는 입력창이 아예 없으므로 먼저
 * 펼쳐야 한다. 그리고 결과는 `role="option"` 이 아니라 **버튼**이다 —
 * 데스크 스펙들이 이미 같은 순서로 하고 있고, 둘 다 처음에 틀려서 90초를
 * 기다리다 죽었다.
 */
export async function pickProject(page: Page, key: string, name = 'E2E'): Promise<void> {
  // 화면 언어는 서버가 기억한다 — 앞선 스펙이 한국어로 바꿔 두었을 수 있다.
  const search = page.getByRole('textbox', { name: /^(project|프로젝트)$/i })
  if (!(await search.isVisible())) {
    await page.getByRole('button', { name: /^(choose…|change|고르세요…|바꾸기)$/i }).click()
  }
  await search.fill(key)
  await page.getByRole('button', { name: `${key} · ${name}` }).click()
  // 고르면 **접힌다.** 접혔다는 것을 보고 나가야 다음 줄이 아직 열려 있는
  // 피커를 건드리지 않는다. 고른 이름 글자로 기다리지 않는 이유가 있다:
  // 그 글자는 접힌 자리에도 목록에도 있어서 전환 중에 둘로 잡힌다.
  await expect(page.getByRole('button', { name: /^(change|바꾸기)$/i })).toBeVisible()
}

export async function createIssue(
  page: Page,
  key: string,
  summary: string,
  options: { priority?: '1' | '2' | '3' | '4' | '5' } = {},
): Promise<string> {
  await page.goto('/issues/new')
  await pickProject(page, key)
  await page.getByLabel(/^summary$/i).fill(summary)
  if (options.priority) await page.getByLabel(/^priority$/i).selectOption(options.priority)
  await page.getByRole('button', { name: /create issue/i }).click()
  await page.waitForURL(new RegExp(`/issues/${key}-`))
  return page.url().split('/').pop() as string
}

/**
 * 마크다운 소스 입력칸.
 *
 * 편집기는 서식 모드가 기본이다. 마크다운을 글자 그대로 넣으려면 소스 탭을
 * 먼저 골라야 한다 — 서식 모드에 `fill` 하면 입력 규칙을 타지 않아서
 * `# 제목` 이 제목이 아니라 글자로 들어앉는다.
 */
export async function bodyField(page: Page): Promise<Locator> {
  const source = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await source.getAttribute('aria-selected')) !== 'true') await source.click()
  // 라벨은 화면마다 다르다(Body·Description). 탭 패널 안의 textarea 로 잡으면
  // 어느 화면에서든 같은 헬퍼가 쓰인다.
  return page.locator('[role="tabpanel"] textarea').first()
}

/** 소스 모드로 본문을 채운다. */
export async function writeBody(page: Page, text: string): Promise<void> {
  await (await bodyField(page)).fill(text)
}

/**
 * 앱이 의도적으로 내는 요청 실패.
 *
 * 콘솔 문자열이 아니라 URL+상태로 거른다. "401 (Unauthorized)" 로 걸러 두면
 * 진짜 401 도 같이 묻힌다.
 */
function isExpectedFailure(url: string, status: number): boolean {
  // 부팅 시 로그인 여부를 서버에 묻는다. 익명이면 401 이 정상이다.
  if (status === 401 && url.includes('/api/v1/auth/me')) return true
  // 개발 서버에는 favicon 이 없다.
  if (status === 404 && url.includes('favicon')) return true
  return false
}

/**
 * 예상 못 한 오류를 실패로 만든다.
 *
 * 화면은 멀쩡해 보이는데 콘솔에만 터지는 오류가 제일 오래 살아남는다.
 * 배열에 담아 두고 테스트가 직접 단언한다 — 4xx 를 일부러 내는 테스트는
 * 자기가 예상하는 것만 빼고 보면 된다.
 */
export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = []
    page.on('response', (response) => {
      const status = response.status()
      if (status < 400) return
      if (isExpectedFailure(response.url(), status)) return
      errors.push(`${String(status)} ${response.url()}`)
    })
    page.on('pageerror', (error) => { errors.push(String(error)); })
    await use(errors)
  },
})

export { expect }
