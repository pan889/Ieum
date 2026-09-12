/**
 * 첫 화면 — 오늘 뭘 해야 하나 (M5 대시보드).
 *
 * 이 화면은 **네 개의 왕복을 엮어** 만든다. 브라우저로 볼 것:
 *
 * - 로그인하면 여기로 온다. 전에는 `/projects` 로 넘겼는데, 프로젝트 목록은
 *   "무엇이 있나" 를 말하고 "오늘 뭘 해야 하나" 를 말하지 않는다.
 * - **늦은 것이 요약 줄에서 보인다.** 목록 아래쪽에 묻혀 있으면 첫 화면이
 *   첫 화면 구실을 못 한다.
 * - 이슈와 문서 태스크가 **같은 자로** 세어진다.
 * - 도는 스프린트는 프로젝트와 남은 양까지 말한다 — 여러 프로젝트를 섞어
 *   보여 주는 칸이라 이름만으로는 누구의 "Cycle 1" 인지 모른다.
 *
 * 개발 스택의 DB 는 앞선 시험들이 남긴 것을 그대로 들고 있다. 그래서 "정확히
 * 한 건" 이 아니라 **내가 만든 것이 있고 숫자가 0 이 아니다**를 본다 — 앞선
 * 시험 수에 따라 붉어지는 시험은 CI 에서 이유 없이 붉어진다.
 */

import type { Page } from '@playwright/test'

import {
  createIssue,
  createProject,
  createSpace,
  expect,
  pickProject,
  projectKey,
  signIn,
  test,
  uniqueKey,
} from './fixtures'

function spaceKey(): string {
  return uniqueKey('H')
}

test('로그인하면 오늘 화면으로 온다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: /^today$/i, level: 1 })).toBeVisible()
  // 네 칸이 다 있다. 하나라도 안 뜨면 그 왕복이 죽은 것이고, 그때 화면은
  // 조용히 반쪽만 보여 준다.
  for (const id of ['home-issues', 'home-tasks', 'home-sprints', 'home-notifications']) {
    await expect(page.getByTestId(id)).toBeAttached()
  }
  await expect(page.getByTestId('home-summary')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('기한이 지난 내 이슈가 요약 줄에서 세어진다', async ({ page, consoleErrors }) => {
  /**
   * **이 시험이 이 화면의 이유다.** 늦은 것이 있는지 알려면 목록을 훑어야
   * 한다면 첫 화면을 여는 뜻이 없다.
   *
   * 숫자를 **전후로 비교한다.** "0 이 아니다" 로는 아무것도 증명되지 않는다 —
   * 개발 스택에는 앞선 시험들이 남긴 늦은 이슈가 이미 여러 건 있다.
   *
   * 그리고 목록에서 **내 줄을 지목하지 않는다.** 칸은 기한이 이른 다섯 줄만
   * 보여 주고, 앞선 실행들이 같은 기한으로 남긴 이슈가 그 자리를 이미 차지
   * 한다 — 그걸 단언하면 실행 순서에 따라 붉어지는 시험이 된다. 대신 칸이
   * **이슈 줄의 모양으로** 그려지는지를 본다(키 링크 + 상태).
   */
  const key = projectKey()
  await signIn(page)

  await page.goto('/')
  await settle(page)
  const before = await overdueCount(page)

  await createProject(page, key)
  await createIssue(page, key, '아주 늦은 일')
  // 기본 워크플로우의 Start progress 는 **실행자를 담당자로 넣는다.**
  await page.getByRole('button', { name: /start progress/i }).click()
  await expect(page.getByRole('button', { name: /^Administrator$/ })).toBeVisible()
  await page.getByLabel(/^due$/i).fill('2019-01-01')
  // 값이 서버까지 갔는지 확인한 뒤에 옮긴다 — 저장은 onChange 로 나간다.
  await expect(page.getByLabel(/^due$/i)).toHaveValue('2019-01-01')

  await page.goto('/')
  await expect.poll(() => overdueCount(page)).toBe(before + 1)

  // 칸이 이슈 줄로 그려진다: 키 링크가 있고 상태가 붙는다.
  const first = page.getByTestId('home-issues').locator('li').first()
  await expect(first.getByRole('link').first()).toHaveText(/-\d+$/)

  expect(consoleErrors).toEqual([])
})

test('문서에 적은 내 할 일도 같은 자로 세어진다', async ({ page, consoleErrors }) => {
  /**
   * **이슈와 태스크를 같은 자로 센다.** 두 곳에서 각자 세면 한쪽만 고쳐지는
   * 날이 오고, 그때 요약과 목록의 합이 안 맞는다.
   *
   * 숫자를 **전후로 비교한다.** "0 이 아니다" 만 보면 이슈만 세고 있어도
   * 초록이다 — 개발 스택에는 앞선 시험들이 남긴 늦은 이슈가 이미 있어서
   * 실제로 그렇게 지나갔고, 되돌려 보니 안 잡혔다.
   *
   */
  const space = spaceKey()
  await signIn(page)

  await page.goto('/')
  await settle(page)
  const before = await overdueCount(page)

  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Home tasks')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeTask(page, '- [ ] 첫 화면에 뜰 일 @')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()

  await page.goto('/')
  const panel = page.getByTestId('home-tasks')
  await expect(panel).toContainText('첫 화면에 뜰 일')
  // 어느 문서의 일인지 함께 보인다 — 없으면 하나씩 눌러 확인해야 한다.
  await expect(panel).toContainText('Home tasks')
  // **딱 하나 늘어야 한다.** 이슈만 세고 있으면 그대로다.
  await expect.poll(() => overdueCount(page)).toBe(before + 1)

  expect(consoleErrors).toEqual([])
})

test('내 일이 든 스프린트가 프로젝트와 남은 양까지 보인다', async ({ page, consoleErrors }) => {
  /**
   * **"도는 것 전부" 가 아니다.** 개발 스택에는 앞선 시험들이 남긴 도는
   * 스프린트가 서른 개 넘게 있다. 그중 다섯을 끝나는 순서로 골라 보여 주는
   * 것은 "내 일" 이 아니라 임의의 목록이고, 이 시험이 자기 주기를 못 찾은
   * 것이 그 사실을 드러냈다 — 그래서 화면 설계를 고쳤다(`list_mine`).
   *
   * 그 뒤에도 **내 줄을 지목하지는 않는다.** 앞선 실행들이 남긴 "내 일 든
   * 주기" 가 이미 다섯 자리를 채우고 있어서, 이번 실행의 프로젝트 키를
   * 단언하면 실행 순서에 따라 붉어진다. 대신 **모든 줄이 같은 모양인지**를
   * 본다: 프로젝트 키 · 이름 · 남은 양. 어느 프로젝트의 주기인지 안 적히는
   * 회귀는 여기서 잡힌다.
   *
   * 누가 들어가는지(내 일이 있어야 뜬다)는 서버 시험 여덟 개가 값으로
   * 붙잡는다 — 그쪽이 정확히 셀 수 있는 자리다.
   */
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, '스프린트에 들어갈 내 일')
  // 기본 워크플로우의 Start progress 는 실행자를 담당자로 넣는다.
  await page.getByRole('button', { name: /start progress/i }).click()
  await expect(page.getByRole('button', { name: /^Administrator$/ })).toBeVisible()

  await page.goto('/sprints')
  await pickProject(page, key)
  await page.getByLabel(/^name$/i).fill('첫 주기')
  await page.getByRole('button', { name: /create sprint/i }).click()
  await expect(page.getByRole('button', { name: '첫 주기', exact: true })).toBeVisible()
  await page.getByLabel(/스프린트에 들어갈 내 일/).check()
  await page.getByLabel(/^sprint$/i).selectOption({ label: '첫 주기' })
  await page.getByRole('button', { name: /add to sprint/i }).click()
  await page.getByRole('button', { name: /^start$/i }).click()
  await expect(page.getByText(/^running$/i)).toBeVisible()

  await page.goto('/')
  const rows = page.getByTestId('home-sprints').locator('li')
  await expect(rows.first()).toBeVisible()
  for (const row of await rows.all()) {
    // 프로젝트 키 · 주기 이름 · 남은 양. 셋이 다 있어야 이 칸이 쓸모 있다.
    //
    // **길이는 키 규칙(2~16자)대로 본다.** 전에는 `{6,}` 이었는데, 그건
    // 시험이 짓는 키(`uniqueKey`)의 길이였지 제품의 규칙이 아니다. 사람이
    // 손으로 만든 짧은 키(`WEB`)가 든 DB 에서는 제품이 멀쩡한데 이 줄만
    // 붉어졌다 — 개발 스택에 스크린샷용 프로젝트를 만들자 바로 그랬다.
    await expect(row).toContainText(/^[A-Z0-9]{2,}/)
    await expect(row).toContainText(/\d+ of \d+ left/i)
  }

  expect(consoleErrors).toEqual([])
})

/**
 * 태스크 한 줄을 쓴다. **멘션은 자동완성으로 골라야** `user:` 링크가 되고,
 * 그래야 담당자가 된다 — 손으로 `@이름` 만 적으면 아무에게도 안 배정된다.
 */
async function writeTask(page: Page, prefix: string): Promise<void> {
  const write = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await write.getAttribute('aria-selected')) !== 'true') await write.click()
  const box = page.locator('[role="tabpanel"] textarea').first()
  await box.click()
  await box.pressSequentially(prefix)
  const people = page.getByRole('listbox', { name: /people to mention/i })
  await expect(people).toBeVisible()
  await people.getByRole('option').first().click()
  await expect(box).toHaveValue(/\[@Administrator\]\(user:[0-9a-f-]+\)/)
  await box.pressSequentially(' due:2019-01-01')
}

/** 요약 줄이 말하는 "늦은 건수". 앞뒤를 비교하려면 숫자로 읽어야 한다. */
async function overdueCount(page: Page): Promise<number> {
  const text = (await page.getByTestId('home-summary').textContent()) ?? ''
  const found = /(\d+)\s+overdue/i.exec(text)
  // 늦은 것이 하나도 없으면 요약 줄은 "조용하다" 를 말한다 — 그때가 0 이다.
  return found === null ? 0 : Number(found[1])
}

/**
 * 첫 화면의 왕복이 끝날 때까지 기다린다.
 *
 * **데이터에 기대지 않는다.** 요약의 낱말을 기다리면 "조용하다" 문장에도
 * overdue 가 들어 있어 로딩 중에 통과하고, 숫자를 기다리면 늦은 것이 하나도
 * 없는 설치에서 영원히 안 온다. 칸은 왕복이 끝나면 **줄이든 빈 문구든** 하나를
 * 그리므로 그 둘 중 하나를 기다린다.
 *
 * **두 칸을 다 기다려야 한다.** 요약은 이슈와 태스크를 함께 세는데, 이슈만
 * 기다리고 숫자를 읽으면 태스크가 아직 안 온 값을 기준으로 잡는다 — 그러면
 * 뒤의 "하나 늘었다" 가 여섯 늘어 보인다. 실제로 그렇게 붉어졌다.
 */
async function settle(page: Page): Promise<void> {
  for (const [id, empty] of [
    ['home-issues', /no issues assigned to you/i],
    ['home-tasks', /no tasks in documents are yours/i],
  ] as const) {
    const rows = page.getByTestId(id).locator('li').first()
    await expect(rows.or(page.getByText(empty)).first()).toBeVisible()
  }
}
