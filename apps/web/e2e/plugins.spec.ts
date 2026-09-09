/**
 * 앱 등록과 확장 지점 (M6 "플러그인 훅").
 *
 * 서버 시험 66개가 규칙을 붙잡고 있다. 브라우저로 한 번 더 보는 이유는 이
 * 기능의 값과 위험이 **화면에서** 드러나기 때문이다:
 *
 * - **앱이 보낸 글이 이슈 화면에 실린다.** 그것이 이 기능의 값이다. 자리를
 *   주고, 앱이 자기 토큰으로 쓰고, 사람이 읽는 왕복을 통째로 본다.
 * - **앱이 보낸 `<script>` 는 실행되지 않는다.** 그것이 이 기능의 위험이고,
 *   이 스펙에서 제일 중요한 시험이다. 마크다운 방언이 원시 HTML 을 끄는
 *   것에 기대고 있는데, 그 한 줄이 무너지면 앱 하나가 이슈를 여는 사람
 *   전부의 세션을 갖는다. 서버 시험으로는 이것을 볼 수 없다 — 실행 여부는
 *   브라우저만 안다.
 * - **누가 쓴 글인지 화면이 말한다.** 우리 글과 앱 글이 구별되지 않으면
 *   앱이 우리 목소리를 빌린다.
 * - **자리를 안 준 앱은 아무것도 못 쓴다.** 앱이 스스로 자리를 만들 수
 *   없다는 것을 실제 HTTP 로 확인한다.
 */

import {
  API,
  createIssue,
  createProject,
  expect,
  signIn,
  signInWithMfa,
  stepUpToken,
  test,
  uniqueKey,
} from './fixtures'

test.slow()

/** 앱을 등록한다. **step-up 이 필요하다** — 앱 등록은 몰래 되지 않는다. */
async function registerApp(
  page: import('@playwright/test').Page,
  slug: string,
): Promise<{ id: string; token: string }> {
  const admin = await stepUpToken(page)
  const made = await page.request.post(`${API}/api/v1/apps`, {
    headers: { Authorization: `Bearer ${admin}` },
    data: { name: `CI ${slug}`, slug },
  })
  expect(made.ok(), await made.text()).toBe(true)
  const body = (await made.json()) as { app: { id: string }; token: string }
  return { id: body.app.id, token: body.token }
}

test.describe('앱', () => {
  test('자리를 주면 앱이 이슈에 글을 쓰고, 사람이 그것을 읽는다', async ({
    page,
    consoleErrors,
  }) => {
    // **2FA 를 통과한 세션으로 시작한다.** 자리를 주는 것은 step-up 이다
    // (`plugins/permissions.py`) — 화면 안에 남의 글을 놓는 자리를 내주는
    // 일이라서 그렇다. 평범한 세션으로 이 화면을 몰면 403 이 난다.
    await signInWithMfa(page)
    const projectKey = uniqueKey('A')
    await createProject(page, projectKey)
    const issueKey = await createIssue(page, projectKey, '배포가 실패했다')

    const slug = `ci-${uniqueKey('x').toLowerCase()}`
    const app = await registerApp(page, slug)
    const admin = await stepUpToken(page)

    // 1. 관리 화면에서 자리를 준다. **앱이 스스로 만들 수 없다.**
    await page.goto('/settings/apps')
    await expect(page.getByRole('heading', { name: /^apps$/i, level: 1 })).toBeVisible()
    const card = page.getByTestId('apps').locator('li').filter({ hasText: slug })
    await expect(card.getByText(/never used its token/i)).toBeVisible()

    await card.getByLabel(/^slot$/i).selectOption('issue.panel')
    await card.getByLabel(/shown as/i).fill('빌드 상태')
    await card.getByRole('button', { name: /give it this place/i }).click()
    await expect(card.getByText('빌드 상태')).toBeVisible()

    // 링크 자리도 하나. 고른 자리가 링크면 주소 칸이 나타난다.
    await card.getByLabel(/^slot$/i).selectOption('issue.link')
    await card.getByLabel(/shown as/i).fill('빌드 보기')
    await card.getByLabel(/link address/i).fill('https://ci.example.com/b/{issue_key}')
    await card.getByRole('button', { name: /give it this place/i }).click()
    await expect(card.getByText('https://ci.example.com/b/{issue_key}')).toBeVisible()

    // 2. 앱이 **자기 토큰으로** 글을 쓴다.
    const wrote = await page.request.put(`${API}/api/v1/apps/self/panels`, {
      headers: { Authorization: `Bearer ${app.token}` },
      data: { issue_key: issueKey, label: '빌드 상태', body: '빌드 **통과** — 3분 12초' },
    })
    expect(wrote.status(), await wrote.text()).toBe(204)

    // 3. 사람이 이슈 화면에서 읽는다. 마크다운이 실제로 그려진다.
    await page.goto(`/issues/${issueKey}`)
    const slots = page.getByTestId('issue-app-slots')
    await expect(slots.getByText(/빌드 통과/)).toBeVisible()
    await expect(slots.locator('strong', { hasText: '통과' })).toBeVisible()
    // **누가 쓴 글인지 말한다.**
    await expect(slots.getByText(new RegExp(`from CI ${slug}`))).toBeVisible()
    // 링크는 자리표가 채워진 주소로 나간다.
    const link = slots.getByRole('link', { name: '빌드 보기' })
    await expect(link).toHaveAttribute('href', `https://ci.example.com/b/${issueKey}`)
    await expect(link).toHaveAttribute('rel', /noopener/)

    // 4. 앱을 끄면 화면에서 사라진다. 끈 것이 정말 꺼져야 한다.
    const off = await page.request.post(`${API}/api/v1/apps/${app.id}/enabled`, {
      headers: { Authorization: `Bearer ${admin}` },
      data: { enabled: false },
    })
    expect(off.ok(), await off.text()).toBe(true)
    await page.reload()
    await expect(page.getByTestId('issue-app-slots')).toBeHidden()

    expect(consoleErrors).toEqual([])
  })

  test('앱이 보낸 스크립트는 실행되지 않는다', async ({ page, consoleErrors }) => {
    /**
     * **이 스펙에서 제일 중요한 시험이다.**
     *
     * 앱이 보낸 글은 마크다운이고, 우리 방언은 원시 HTML 을 끈다
     * (`dialect.ts` 의 `html: false`). 그 한 줄이 무너지면 앱 하나가 이슈를
     * 여는 사람 전부의 세션을 갖는다 — 그래서 실행되지 않는다는 것을
     * 브라우저에서 직접 본다. 서버 시험으로는 볼 수 없는 성질이다.
     */
    await signIn(page)
    const projectKey = uniqueKey('B')
    await createProject(page, projectKey)
    const issueKey = await createIssue(page, projectKey, '스크립트를 보내 본다')

    const slug = `evil-${uniqueKey('x').toLowerCase()}`
    const app = await registerApp(page, slug)
    const admin = await stepUpToken(page)

    const placed = await page.request.post(`${API}/api/v1/apps/${app.id}/slots`, {
      headers: { Authorization: `Bearer ${admin}` },
      data: { slot: 'issue.panel', kind: 'panel', label: '상태' },
    })
    expect(placed.ok(), await placed.text()).toBe(true)

    // 스크립트를 심어 본다. 실행되면 `window.__pwned` 가 생긴다.
    const wrote = await page.request.put(`${API}/api/v1/apps/self/panels`, {
      headers: { Authorization: `Bearer ${app.token}` },
      data: {
        issue_key: issueKey,
        label: '상태',
        body: '<script>window.__pwned = true</script><img src=x onerror="window.__pwned = true">\n\n[누르지 마세요](javascript:window.__pwned=true)',
      },
    })
    expect(wrote.status(), await wrote.text()).toBe(204)

    await page.goto(`/issues/${issueKey}`)
    const slots = page.getByTestId('issue-app-slots')
    await expect(slots).toBeVisible()

    // **아무것도 실행되지 않았다.**
    expect(await page.evaluate(() => '__pwned' in window)).toBe(false)
    // 이미지 태그가 DOM 에 들어오지도 않았다 — 글자로 남았다.
    expect(await slots.locator('img').count()).toBe(0)
    expect(await slots.locator('script').count()).toBe(0)
    // `javascript:` 링크는 앵커가 되지 못한다.
    expect(await slots.locator('a[href^="javascript:"]').count()).toBe(0)

    expect(consoleErrors).toEqual([])
  })

  test('자리를 안 준 앱은 아무것도 쓸 수 없다', async ({ page }) => {
    /** 앱이 스스로 자리를 만들 수 있으면 등록이라는 것이 뜻을 잃는다. */
    await signIn(page)
    const projectKey = uniqueKey('C')
    await createProject(page, projectKey)
    const issueKey = await createIssue(page, projectKey, '자리 없는 앱')

    const slug = `bare-${uniqueKey('x').toLowerCase()}`
    const app = await registerApp(page, slug)

    const refused = await page.request.put(`${API}/api/v1/apps/self/panels`, {
      headers: { Authorization: `Bearer ${app.token}` },
      data: { issue_key: issueKey, label: '안 받은 자리', body: '글' },
    })
    expect(refused.status()).toBe(403)
    const body = (await refused.json()) as { error: { code: string } }
    expect(body.error.code).toBe('plugins.no_such_placement')

    // 화면에도 아무 자리가 안 생긴다.
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByTestId('issue-app-slots')).toBeHidden()
  })
})
