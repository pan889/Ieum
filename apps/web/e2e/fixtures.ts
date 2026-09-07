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
  options: { grants?: string[] } = {},
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
    data: { email: user.email, display_name: user.displayName, locale: 'en' },
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

export async function createIssue(
  page: Page,
  key: string,
  summary: string,
  options: { priority?: '1' | '2' | '3' | '4' | '5' } = {},
): Promise<string> {
  await page.goto('/issues/new')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
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
