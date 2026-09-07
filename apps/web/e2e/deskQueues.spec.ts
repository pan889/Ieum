/**
 * 큐와 정형 응답 (feature-map C3, C10).
 *
 * 서버 시험이 규칙을 이미 붙잡고 있다(`test_desk_queues.py`). 여기서 한 번
 * 더 보는 이유는 **화면이 그 규칙을 사람에게 전달하는지**가 별개이기 때문이다:
 *
 * - 조건이 틀렸을 때 화면이 "결과 없음" 이라고 말하면, 조건을 잘못 적은
 *   관리자는 티켓이 없다고 믿는다 — 그 사이 그 큐로 들어와야 할 요청은
 *   아무도 안 본다. 저장 자체가 거절되고 **어디가 틀렸는지** 보여야 한다.
 * - 정형 응답을 끼워 넣는 것이 쓰던 글을 지우면 되돌릴 수 없다.
 */

import { createPortal, expect, signIn, signInAsCustomer, test } from './fixtures'
import type { Page } from '@playwright/test'

test.slow()

/** 큐 화면을 열고 이 프로젝트를 고른다. */
async function openDesk(page: Page, projectKey: string): Promise<void> {
  await page.goto('/desk')
  await expect(page.getByRole('heading', { name: /queues/i, level: 1 })).toBeVisible()
  // 기본값은 "첫 프로젝트" 다. 우리 프로젝트를 검색으로 고른다 — 목록을
  // 통째로 드롭다운에 넣지 않는다는 판단이 화면에 그대로 있다.
  //
  // **피커는 고른 뒤 접힌다.** 먼저 펼쳐야 입력창이 생긴다 — 처음에는
  // 곧바로 `getByLabel('Project')` 에 쳤고, 90초를 기다리다 죽었다.
  // 접힌 상태에서는 고른 프로젝트와 "Change" 버튼만 있다.
  // 결과는 **버튼**이다(`role="option"` 이 아니다). 접힌 상태를 먼저 펼치고,
  // 입력창에 키를 쳐서 후보를 좁힌 다음 그 버튼을 누른다 — `portal.spec.ts`
  // 가 이미 같은 순서로 한다. 처음에는 `getByRole('option')` 로 집으려 했고
  // 90초를 기다리다 죽었다.
  await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
  await page.getByRole('textbox', { name: /^project$/i }).fill(projectKey)
  await page.getByRole('button', { name: new RegExp(projectKey) }).click()
}

/**
 * 티켓이 **아닌** 이슈 하나. 큐가 그것을 걸러내는지 보는 데 쓴다.
 *
 * 화면이 아니라 API 로 만든다: `createIssue` 픽스처는 프로젝트 이름이
 * `E2E` 라고 전제하고, `createPortal` 이 만드는 프로젝트는 그렇지 않다.
 */
async function createPlainIssue(page: Page, projectId: string): Promise<void> {
  // `accessToken` 은 내보내지 않는다. 2FA 를 통과하지 않은 시드 관리자
  // 토큰이 `plainAdminToken` 으로 나와 있고, 이슈 생성에는 step-up 이 걸려
  // 있지 않으므로 그것으로 충분하다.
  const { API, plainAdminToken } = await import('./fixtures')
  const token = await plainAdminToken(page)
  const headers = { Authorization: `Bearer ${token}` }
  const types = await page.request.get(
    `${API}/api/v1/issues/types?project_id=${projectId}`,
    { headers },
  )
  expect(types.ok(), await types.text()).toBe(true)
  const first = ((await types.json()) as { id: string }[])[0]
  expect(first, '프로젝트에 이슈 유형이 없다').toBeTruthy()
  const created = await page.request.post(`${API}/api/v1/issues`, {
    headers,
    data: {
      project_id: projectId,
      type_id: (first as { id: string }).id,
      summary: '평범한 이슈',
    },
  })
  expect(created.ok(), await created.text()).toBe(true)
}

async function addQueue(page: Page, name: string, iql: string): Promise<void> {
  await page.getByRole('button', { name: /new queue/i }).click()
  await page.getByLabel(/^name$/i).fill(name)
  await page.getByLabel(/^condition$/i).fill(iql)
  await page.getByRole('button', { name: /^save$/i }).click()
}

test.describe('큐', () => {
  test('조건을 저장하면 그 조건에 맞는 티켓이 담긴다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)

    // 고객이 요청 하나를 낸다 — 큐에 담길 티켓이 있어야 한다.
    const customer = await import('./fixtures').then((m) =>
      m.inviteUser(page, { isCustomer: true }),
    )
    const context = await page.context().browser()?.newContext()
    expect(context, '브라우저 핸들이 없다').toBeTruthy()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('큐에 담길 요청')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('큐에 담길 요청')
    await (context as NonNullable<typeof context>).close()

    // `inviteUser` 가 이 페이지를 초대 수락으로 보내므로 다시 로그인한다.
    await signIn(page)
    await openDesk(page, portal.projectKey)
    await addQueue(page, '열린 요청', `project = ${portal.projectKey}`)

    // 조건을 화면이 그대로 보여 준다 — 목록이 뜻밖일 때 사람이 볼 것은 조건이다.
    await expect(page.getByText(`project = ${portal.projectKey}`)).toBeVisible()
    await expect(page.getByText('큐에 담길 요청')).toBeVisible()
  })

  test('조건이 틀리면 저장을 거절하고 어디가 틀렸는지 말한다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)
    await openDesk(page, portal.projectKey)

    // `assigne` 는 `assignee` 의 오타다. 저장되면 이 큐는 누를 때만 실패하고,
    // 만든 사람은 자기 큐를 눌러 보지 않는다.
    await addQueue(page, '틀린 큐', 'assigne = me')

    // **필드 이름을 말해야 한다.** "질의가 틀렸다" 로는 고칠 수 없다.
    //
    // `getByRole('alert')` 로 집는다. 처음에는 `getByText(/assigne/)` 였고
    // 그건 오류 문구와 **입력창에 남은 글자** 둘을 함께 집어 strict 위반이
    // 됐다 — 제품은 멀쩡했다. 덤으로 이 단언은 그 문구가 alert 로
    // 알려지는지까지 본다(스크린 리더가 읽어야 한다).
    await expect(page.getByRole('alert')).toContainText(/assigne/)
    // 그리고 저장되지 않았다 — 큐 버튼이 생기지 않는다.
    await expect(page.getByRole('button', { name: '틀린 큐' })).toHaveCount(0)
  })

  test('큐에는 티켓만 담긴다', async ({ page }) => {
    // **이 시험이 이 파일의 이유다.** 조건은 프로젝트 하나뿐이라 평범한
    // 이슈도 다 잡는데, 큐에는 티켓만 나와야 한다 — 평범한 이슈에는 요청자도
    // 창구도 없어서 상담원 화면이 반쯤 빈 채로 그려진다.
    await signIn(page)
    const portal = await createPortal(page)
    // **API 로 만든다.** `createIssue` 는 프로젝트 이름이 `E2E` 라고
    // 전제하는데(`${key} · E2E` 옵션을 고른다) `createPortal` 은 `Desk …`
    // 로 만든다. 화면을 거쳐 만들 이유도 없다 — 이 시험이 보는 것은 큐가
    // 무엇을 담는가이고, 이슈를 만드는 화면은 다른 스펙이 이미 본다.
    await createPlainIssue(page, portal.projectId)

    await openDesk(page, portal.projectKey)
    await addQueue(page, '전체', `project = ${portal.projectKey}`)

    await expect(page.getByText(/nothing in this queue/i)).toBeVisible()
    await expect(page.getByText('평범한 이슈')).toHaveCount(0)
  })
})

test.describe('정형 응답', () => {
  test('만들어 두면 티켓 코멘트에 끼워 넣을 수 있다', async ({ page }) => {
    await signIn(page)
    const portal = await createPortal(page)

    const customer = await import('./fixtures').then((m) =>
      m.inviteUser(page, { isCustomer: true }),
    )
    const context = await page.context().browser()?.newContext()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('정형 응답 확인용')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('정형 응답 확인용')
    const shown = await customerPage.locator('body').innerText()
    const found = /\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown)
    expect(found, '티켓 키를 화면에서 찾지 못했다').toBeTruthy()
    const issueKey = (found as RegExpExecArray)[1] as string
    await (context as NonNullable<typeof context>).close()

    await signIn(page)
    await openDesk(page, portal.projectKey)
    await page.getByRole('button', { name: /new response/i }).click()
    await page.getByLabel(/^name$/i).fill('환불 안내')
    await page.getByLabel(/^shortcut$/i).fill('/환불')
    await page.getByLabel(/^text$/i).fill('영업일 3일 안에 처리됩니다.')
    await page.getByRole('button', { name: /^save$/i }).click()
    // 앞의 `/` 는 떼어 저장한다 — 그대로 두면 `//환불` 로 불린다.
    await expect(page.getByText('/환불', { exact: true })).toBeVisible()

    // 티켓에서 끼워 넣는다.
    await page.goto(`/issues/${issueKey}`)
    await expect(page.getByText(/insert a canned response/i)).toBeVisible()
    await page.getByRole('button', { name: /환불 안내/ }).click()
    await expect(page.getByLabel(/^comments$/i)).toContainText('영업일 3일 안에 처리됩니다')
  })

  test('끼워 넣기는 쓰던 글을 지우지 않는다', async ({ page }) => {
    // 덮어쓰면 되돌릴 수 없다 — 편집기의 실행 취소는 프로그램이 바꾼 값까지
    // 되돌려 주지 않는다. 그리고 상담원은 보통 "인사 + 정형 문구" 로 쓴다.
    await signIn(page)
    const portal = await createPortal(page)

    const customer = await import('./fixtures').then((m) =>
      m.inviteUser(page, { isCustomer: true }),
    )
    const context = await page.context().browser()?.newContext()
    const customerPage = await (context as NonNullable<typeof context>).newPage()
    await signInAsCustomer(customerPage, customer)
    await customerPage.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await customerPage.getByLabel(new RegExp(portal.formLabel)).fill('이어붙이기 확인용')
    await customerPage.getByRole('button', { name: /send request/i }).click()
    await expect(customerPage.getByRole('heading', { level: 1 })).toContainText('이어붙이기 확인용')
    const shown = await customerPage.locator('body').innerText()
    const issueKey = (/\b([A-Z][A-Z0-9]*-\d+)\b/.exec(shown) as RegExpExecArray)[1] as string
    await (context as NonNullable<typeof context>).close()

    await signIn(page)
    await openDesk(page, portal.projectKey)
    await page.getByRole('button', { name: /new response/i }).click()
    await page.getByLabel(/^name$/i).fill('마무리 인사')
    await page.getByLabel(/^text$/i).fill('더 필요한 것이 있으면 알려 주세요.')
    await page.getByRole('button', { name: /^save$/i }).click()
    await expect(page.getByText('마무리 인사')).toBeVisible()

    await page.goto(`/issues/${issueKey}`)
    const editor = page.getByLabel(/^comments$/i)
    await editor.click()
    await editor.pressSequentially('안녕하세요,')
    await page.getByRole('button', { name: /마무리 인사/ }).click()

    // 쓰던 글이 **남아 있고** 정형 문구가 뒤에 붙었다.
    await expect(editor).toContainText('안녕하세요,')
    await expect(editor).toContainText('더 필요한 것이 있으면 알려 주세요')
  })
})
