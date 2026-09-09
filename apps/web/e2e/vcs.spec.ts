/**
 * 코드 저장소 연동 (feature-map A22).
 *
 * 서버 시험이 서명·파싱·경계를 이미 붙잡고 있다. 브라우저로 한 번 더 보는
 * 이유는 이 기능이 **두 화면과 한 문**으로 이루어져 있고, 그 셋이 이어져야만
 * 쓸 수 있기 때문이다:
 *
 * - 관리 화면이 **시크릿과 완성된 주소**를 준다. 경로만 주면 사람이 도메인을
 *   손으로 붙이고 `/api` 를 빠뜨린다.
 * - 그 시크릿으로 서명한 푸시가 **이슈 상세에 보인다.** 서버가 링크를 만들어
 *   놓고 화면이 안 그리면 아무도 모른다.
 * - **아직 아무것도 못 받았으면 목록이 그렇게 말한다.** 이 연동은 조용히
 *   고장나고(코드 호스트 쪽 설정이 틀렸을 때), 그건 이 줄에서만 보인다.
 *
 * 등록은 step-up 대상이라 **2FA 를 통과한 관리자로** 들어간다.
 */

import { createHmac } from 'node:crypto'

import type { Page } from '@playwright/test'

import {
  API,
  createIssue,
  createProject,
  expect,
  pickProject,
  signInWithMfa,
  test,
  uniqueKey,
} from './fixtures'

test.slow()

interface Connected {
  /** 화면이 보여 준 완성된 주소. */
  url: string
  secret: string
}

/** 저장소를 등록하고, 한 번만 보이는 두 값을 화면에서 읽어 온다. */
async function connect(page: Page, projectKey: string, name: string): Promise<Connected> {
  await page.goto('/settings/repositories')
  await expect(page.getByRole('heading', { name: /code repositories/i, level: 1 })).toBeVisible()
  await pickProject(page, projectKey)
  await page.getByLabel(/^repository$/i).fill(name)
  await page.getByRole('button', { name: /^connect$/i }).click()

  const card = page.getByTestId('vcs-issued')
  await expect(card).toBeVisible()
  // 값은 폼 컨트롤이 아니라 `<code>` 다. 라벨로 집으면 그 옆의 복사 버튼이
  // 잡힌다 — 처음에 그렇게 썼고 "Copy" 를 주소로 읽어 왔다.
  const url = await card.getByTestId('vcs-webhook-url').innerText()
  const secret = await card.getByTestId('vcs-secret').innerText()
  expect(secret).not.toBe('')
  return { url: url.trim(), secret: secret.trim() }
}

/**
 * 코드 호스트 흉내를 낸다. **브라우저가 아니라 서명으로** 들어가는 문이다.
 *
 * 몸을 문자열로 만들어 그 **글자에** 서명한다. 객체를 넘기면 Playwright 가
 * 직렬화하는데, 그 결과가 우리가 서명한 글자와 한 글자라도 다르면 서명이
 * 어긋난다 — 그게 이 문의 요점이므로 여기서 흔들리면 안 된다.
 */
async function deliver(
  page: Page,
  connected: Connected,
  message: string,
  options: { sha?: string; secret?: string; event?: string } = {},
) {
  const sha = options.sha ?? uniqueKey('a').toLowerCase().padEnd(40, '0')
  const body = JSON.stringify({
    ref: 'refs/heads/main',
    commits: [
      {
        id: sha,
        message,
        url: `https://github.example.com/acme/web/commit/${sha}`,
        timestamp: new Date().toISOString(),
        author: { username: 'sujin', name: 'Sujin' },
      },
    ],
  })
  const signature = createHmac('sha256', options.secret ?? connected.secret)
    .update(body)
    .digest('hex')
  // 화면이 준 주소는 앱 오리진이다(같은 도메인에서 서빙한다). 개발 스택은
  // API 가 따로 떠 있으므로 경로만 떼어 그쪽으로 보낸다 — 같은 라우트다.
  const path = new URL(connected.url).pathname
  return page.request.post(`${API}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      'X-GitHub-Event': options.event ?? 'push',
      'X-Hub-Signature-256': `sha256=${signature}`,
    },
    data: body,
  })
}

test.describe('저장소 연동', () => {
  test('서명한 푸시가 이슈 상세의 개발 칸에 붙는다', async ({ page, consoleErrors }) => {
    await signInWithMfa(page)
    const key = uniqueKey('V')
    await createProject(page, key)
    const issueKey = await createIssue(page, key, '로그인이 안 된다')

    const connected = await connect(page, key, `acme/${key.toLowerCase()}`)
    // **완성된 주소를 준다.** 경로만 주면 사람이 도메인을 손으로 붙인다.
    expect(connected.url).toContain('/api/v1/vcs/github/')
    expect(connected.url.startsWith('http')).toBe(true)

    const sha = 'abc1234'.padEnd(40, '0')
    const sent = await deliver(page, connected, `fixes ${issueKey} 로그인을 고친다`, { sha })
    expect(sent.status(), await sent.text()).toBe(200)
    expect(await sent.json()).toEqual({ received: 1, linked: 1 })

    await page.goto(`/issues/${issueKey}`)
    const panel = page.getByTestId('issue-development')
    await expect(panel).toBeVisible()
    // SHA 는 앞 일곱 자만 보여 준다 — 사람이 그 길이로 말한다.
    await expect(panel.getByRole('link', { name: 'abc1234' })).toBeVisible()
    await expect(panel.getByText('로그인을 고친다')).toBeVisible()
    // "닫는다고 적었다" 는 표시일 뿐이다. 상태는 그대로여야 한다.
    await expect(panel.getByText(/says it closes this/i)).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('로그인이 안 된다')

    expect(consoleErrors).toEqual([])
  })

  test('서명이 틀리면 아무것도 붙지 않는다', async ({ page }) => {
    await signInWithMfa(page)
    const key = uniqueKey('V')
    await createProject(page, key)
    const issueKey = await createIssue(page, key, '고쳐야 한다')

    const connected = await connect(page, key, `acme/${key.toLowerCase()}`)
    const refused = await deliver(page, connected, `${issueKey} 몰래 붙인다`, {
      secret: 'not-the-secret',
    })
    expect(refused.status(), await refused.text()).toBe(401)

    // 붙은 것이 없으면 칸 자체가 없다 — 빈 칸을 두지 않는다.
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByRole('heading', { level: 1 })).toContainText('고쳐야 한다')
    await expect(page.getByTestId('issue-development')).toHaveCount(0)
  })

  test('아직 아무것도 못 받았으면 목록이 그렇게 말한다', async ({ page }) => {
    await signInWithMfa(page)
    const key = uniqueKey('V')
    await createProject(page, key)

    const connected = await connect(page, key, `acme/${key.toLowerCase()}`)
    const rows = page.getByTestId('repositories')
    await expect(rows.getByText(/nothing received yet/i)).toBeVisible()

    // GitHub 은 웹훅을 만들 때 `ping` 을 먼저 보낸다. 링크는 안 생기지만
    // **받았다는 사실**은 남아야 한다 — 그게 배선이 끝났다는 신호다.
    const ping = await deliver(page, connected, 'ignored', { event: 'ping' })
    expect(ping.status(), await ping.text()).toBe(200)

    await page.reload()
    await pickProject(page, key)
    await expect(rows.getByText(/last received/i)).toBeVisible()
    await expect(rows.getByText(/nothing received yet/i)).toHaveCount(0)
  })
})
