/**
 * 반복 이슈 — 스케줄로 이슈를 만든다 (feature-map A27).
 *
 * 스케줄 계산은 서버 시험 23개가 값으로 붙잡고, 실행 규칙은 13개가 붙잡는다.
 * 브라우저로 보는 것은 **사슬이 이어졌는가**와, 화면이 사람에게 필요한 것을
 * 말하는가다:
 *
 * - 만들면 목록에 뜨고 **다음에 도는 시각과 시간대**가 보인다. 반복은 아무
 *   일도 안 일어나는 방식으로 고장나므로, 그 줄이 유일한 신호다.
 * - **켠 즉시 이슈가 생기지 않는다.** 시험용으로 켠 스케줄이 실제 이슈를
 *   내면 사람은 그걸 지우고 기능을 안 쓴다.
 * - 끄면 끈 것으로 보인다.
 * - **담당자가 붙는다.** 반복으로 만들어진 이슈에 주인이 없으면 아무도 안
 *   보고, 그게 반복이 조용히 쓸모없어지는 길이다.
 */

import {
  createIssue,
  createProject,
  expect,
  pickProject,
  projectKey,
  signIn,
  test,
} from './fixtures'

test('반복을 만들면 다음 시각과 시간대가 보인다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  // 프로젝트 피커가 최근 항목을 찾을 수 있게 이슈 하나를 만들어 둔다.
  await createIssue(page, key, '아무 일')

  await page.goto('/recurrences')
  await expect(page.getByRole('heading', { name: /repeating issues/i })).toBeVisible()
  await pickProject(page, key)

  await page.getByLabel(/^repeat name$/i).fill('주간 점검')
  await page.getByLabel(/^issue summary$/i).fill('서버 상태를 본다')
  await page.getByLabel(/^repeats$/i).selectOption('weekly')
  await page.getByLabel(/^weekday$/i).selectOption('0')
  await page.getByLabel(/^hour$/i).fill('9')
  await page.getByLabel(/^time zone$/i).fill('Asia/Seoul')
  await page.getByRole('button', { name: /^create$/i }).click()

  const list = page.getByTestId('recurrences')
  await expect(list).toContainText('주간 점검')
  await expect(list).toContainText('서버 상태를 본다')
  // **시간대를 함께 보여 준다.** "월요일 09:00" 만 적으면 보는 사람은 자기
  // 시간대의 9시로 읽는다.
  await expect(list).toContainText('09:00 (Asia/Seoul)')
  await expect(list).toContainText(/next:/i)

  expect(consoleErrors).toEqual([])
})

test('만든 즉시 이슈가 생기지는 않는다', async ({ page, consoleErrors }) => {
  /**
   * 처음 실행도 지금 뒤다. 만든 즉시 한 건이 나오면 사람은 시험용으로 켰다가
   * 실제 이슈를 받고, 그다음부터 이 기능을 안 쓴다.
   */
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, '이미 있던 일')

  await page.goto('/recurrences')
  await pickProject(page, key)
  await page.getByLabel(/^repeat name$/i).fill('매일 점검')
  await page.getByLabel(/^issue summary$/i).fill('오늘의 점검')
  await page.getByLabel(/^repeats$/i).selectOption('daily')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByTestId('recurrences')).toContainText('매일 점검')

  // 이슈 목록에는 아직 그 요약이 없다.
  await page.goto(`/issues?iql=${encodeURIComponent(`project = ${key}`)}`)
  await expect(page.getByText('이미 있던 일')).toBeVisible()
  await expect(page.getByText('오늘의 점검')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('끄면 끈 것으로 보인다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, '아무 일')

  await page.goto('/recurrences')
  await pickProject(page, key)
  await page.getByLabel(/^repeat name$/i).fill('멈출 반복')
  await page.getByLabel(/^issue summary$/i).fill('안 만들어질 이슈')
  await page.getByRole('button', { name: /^create$/i }).click()

  const list = page.getByTestId('recurrences')
  await expect(list).toContainText(/next:/i)
  await list.getByRole('button', { name: /^turn off$/i }).click()

  // **꺼진 것은 "안 돈다" 고 말한다.** 다음 시각만 남겨 두면 도는 줄 안다.
  await expect(list).toContainText(/will not run/i)
  await expect(list).toContainText(/^(?!.*next:).*$/s)

  // **켜고 끄기가 쓰던 폼을 지우지 않는다.** 한 줄을 고치는 중에 다른 줄을
  // 끄면 쓰던 것이 사라지는데, 그건 사람이 다시 안 쓰는 종류의 일이다.
  await list.getByRole('button', { name: /^edit$/i }).click()
  await expect(page.getByLabel(/^repeat name$/i)).toHaveValue('멈출 반복')
  await list.getByRole('button', { name: /^turn on$/i }).click()
  await expect(list).toContainText(/next:/i)
  await expect(page.getByLabel(/^repeat name$/i)).toHaveValue('멈출 반복')

  expect(consoleErrors).toEqual([])
})

test('담당자를 정하면 목록에 그 사람이 보이고, 검색어를 바꿔도 안 지워진다', async ({
  page,
  consoleErrors,
}) => {
  /**
   * **이 시험의 뒷부분이 요점이다.**
   *
   * 담당자 선택기의 후보는 스무 명까지고 검색어로 걸러 온다. 고른 사람이 그
   * 목록에서 빠지면 `<option>` 이 사라지고, 브라우저는 값을 가진 옵션이 없는
   * `<select>` 의 값을 **버린다** — 그러면 저장 버튼을 누르는 순간 담당자가
   * 조용히 없어진다. 화면은 아무 말도 하지 않는다.
   */
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, '아무 일')

  await page.goto('/recurrences')
  await pickProject(page, key)
  await page.getByLabel(/^repeat name$/i).fill('담당 있는 반복')
  await page.getByLabel(/^issue summary$/i).fill('주인이 있는 일')
  await page.getByLabel(/^assignee$/i).selectOption({ label: 'Administrator' })
  await page.getByLabel(/^labels$/i).fill('점검, 주간')
  await page.getByRole('button', { name: /^create$/i }).click()

  const list = page.getByTestId('recurrences')
  await expect(list).toContainText('담당 있는 반복')
  await expect(list).toContainText('Administrator')
  await expect(list).toContainText('점검, 주간')

  // 고치기로 열면 고른 사람이 그대로 있다.
  await list.getByRole('button', { name: /^edit$/i }).click()
  const assignee = page.getByLabel(/^assignee$/i)
  await expect(assignee).toHaveValue(/.+/)
  const chosen = await assignee.inputValue()

  // 아무도 안 걸리는 검색어를 넣는다. 후보가 비어도 고른 사람은 남아야 한다.
  await page.getByLabel(/^find someone$/i).fill('zzzz-nobody')
  await expect(assignee).toHaveValue(chosen)
  await expect(assignee.locator('option', { hasText: 'Administrator' })).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})
