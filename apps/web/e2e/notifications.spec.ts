/**
 * 구독과 알림함.
 *
 * 남에게 알림이 실제로 가는지는 서버 테스트가 본다(수신자가 둘 필요하다).
 * 여기서는 브라우저 없이 못 보는 것만 본다 — 구독이 새로고침을 넘어 남는가,
 * 그리고 **자기 행동이 자기에게 오지 않는가**.
 */

import {
  API,
  createIssue,
  createProject,
  createSpace,
  expect,
  plainAdminToken,
  signIn,
  test,
  uniqueKey,
  writeBody,
} from './fixtures'

function spaceKey(): string {
  return uniqueKey('B')
}

/** 아웃박스는 15초마다 훑는다. 알림이 오는지 보려면 그보다 더 기다려야 한다. */
const SWEEP_MS = 20_000

async function newPage(
  page: import('@playwright/test').Page,
  title: string,
  body: string,
): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.locator('.ieum-markdown').first()).toBeVisible()
}

test('스페이스 구독은 새로고침해도 남는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  const watch = page.getByRole('button', { name: /^watch$/i })
  await expect(watch).toBeVisible()
  await watch.click()
  // 눌린 상태를 낙관적으로 그리지 않는다 — 서버가 그렇다고 해야 그렇다.
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.getByRole('button', { name: /^watching$/i }).click()
  await expect(page.getByRole('button', { name: /^watch$/i })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('이슈도 상세 화면에서 구독한다', async ({ page, consoleErrors }) => {
  /**
   * **이 단추가 없어서 이슈를 지켜볼 방법이 없었다.**
   *
   * 이슈 알림은 담당자·보고자·지목된 사람에게 간다 — 전부 이벤트에 실려 오는
   * 값이다. 그래서 남의 이슈를 지켜보려면 코멘트를 하나 달아 그 자리에
   * 들어가는 수밖에 없었는데, 그건 구독이 아니라 남의 이슈에 글을 쓰는 일이다.
   * 문서에는 처음부터 이 단추가 있었다.
   *
   * 알림이 실제로 오는지는 서버 시험이 본다(`test_notify.py`). 여기서 보는
   * 것은 **화면에 손잡이가 있고, 누른 것이 서버에 남는가** 다.
   */
  const key = uniqueKey('W')
  await signIn(page)
  await createProject(page, key)
  const issueKey = await createIssue(page, key, '디스크가 찼다')
  await page.goto(`/issues/${issueKey}`)

  const watch = page.getByRole('button', { name: /^watch$/i })
  await expect(watch).toBeVisible()
  await watch.click()
  // 눌린 상태를 낙관적으로 그리지 않는다 — 서버가 그렇다고 해야 그렇다.
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await page.getByRole('button', { name: /^watching$/i }).click()
  await expect(page.getByRole('button', { name: /^watch$/i })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('문서도 따로 구독한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await newPage(page, 'Runbook', '배포 절차를 적는다.')

  // 스페이스 구독 버튼과 문서 구독 버튼이 둘 다 있다. 문서 쪽은 본문 옆이다.
  const onPage = page.getByRole('article').getByRole('button', { name: /^watch$/i })
  await onPage.click()
  await expect(
    page.getByRole('article').getByRole('button', { name: /^watching$/i }),
  ).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('프로젝트도 목록에서 구독한다 — 그리고 한 번만 묻는다', async ({
  page,
  consoleErrors,
}) => {
  /**
   * **프로젝트 구독은 오래 저장만 되고 있었다.** 서버가 안 읽었고(고쳤다),
   * 화면에는 손잡이가 없었다.
   *
   * 목록에 달면서 조심한 것: 행마다 구독 상태를 물으면 한 페이지에 스무
   * 번이다. 그래서 한 번에 묻는 문을 먼저 냈고, **이 시험이 그것을
   * 지킨다** — `/watches/status`(하나짜리)가 한 번도 안 불려야 한다.
   */
  const key = uniqueKey('V')
  await signIn(page)
  await createProject(page, key)

  const single: string[] = []
  const bulk: string[] = []
  page.on('request', (request) => {
    const url = request.url()
    if (url.includes('/api/v1/watches/statuses')) bulk.push(url)
    else if (url.includes('/api/v1/watches/status')) single.push(url)
  })

  await page.goto('/projects')
  await page.getByLabel(/find a project/i).fill(key)
  const row = page.getByTestId('projects').locator('li').filter({ hasText: key })
  await expect(row).toHaveCount(1)

  // 눌러서 구독하고, 새로고침해도 남는지 본다.
  await row.getByRole('button', { name: /^watch$/i }).click()
  await expect(row.getByRole('button', { name: /^watching$/i })).toBeVisible()
  await page.reload()
  await page.getByLabel(/find a project/i).fill(key)
  await expect(row.getByRole('button', { name: /^watching$/i })).toBeVisible()

  // **하나짜리 질의는 한 번도 안 나갔다.** 목록은 통째로 묻는다.
  expect(single).toEqual([])
  expect(bulk.length).toBeGreaterThan(0)

  expect(consoleErrors).toEqual([])
})

test('내가 한 일은 나에게 알리지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)

  /**
   * **이 시험이 기대는 환경설정을 스스로 맞춘다.**
   *
   * `notify_own_actions` 가 켜져 있으면 이 알림은 **와야 맞다**(그게 그
   * 스위치가 하는 일이다). 시드 DB 를 공유하는데 이 시험은 그 값을 안 정하고
   * 껐다고 가정하고 있었다 — 어쩌다 켜진 DB 에서는 제품이 맞게 동작하는데
   * 시험만 붉어졌다. 실제로 그렇게 붉어진 것을 붙잡고 여기까지 왔다.
   *
   * 화면에는 이 스위치가 없어서(카탈로그에 낱말만 있다) API 로 맞춘다.
   */
  const token = await plainAdminToken(page)
  const set = await page.request.patch(`${API}/api/v1/notifications/preferences`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { notify_own_actions: false },
  })
  expect(set.ok(), await set.text()).toBe(true)

  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^watch$/i }).click()
  await expect(page.getByRole('button', { name: /^watching$/i })).toBeVisible()

  await newPage(page, 'Runbook', '배포 절차를 적는다.')

  // 구독해 두었으니 알림이 만들어질 자리다. 그런데 쓴 사람은 나다.
  await page.waitForTimeout(SWEEP_MS)
  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: /notifications/i })).toBeVisible()
  await expect(page.getByText(new RegExp(key))).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('메일 받는 방식은 고른 대로 남는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/notifications')

  // 켬/끔 하나로 두면 알림마다 한 통씩 오고 사람들은 통째로 끈다. 끈
  // 사람에게는 아무것도 못 알린다 — 그래서 가운데 칸이 있다.
  const mode = page.getByLabel(/^email$/i)
  await expect(mode).toHaveValue('instant')
  await mode.selectOption('daily')
  await expect(page.getByText(/^saved\.$/i)).toBeVisible()

  await page.reload()
  await expect(page.getByLabel(/^email$/i)).toHaveValue('daily')

  // 시드 DB 를 공유하므로 되돌려 놓는다.
  await page.getByLabel(/^email$/i).selectOption('instant')
  await expect(page.getByText(/^saved\.$/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
