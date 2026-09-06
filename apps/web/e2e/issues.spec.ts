import { createIssue, createProject, expect, projectKey, signIn, test, writeBody } from './fixtures'

test('이슈를 만들고 전이·코멘트·담당자를 다룬다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'first issue')

  // 전이. 기본 워크플로우의 Start progress 는 실행자를 담당자로 넣는다.
  await page.getByRole('button', { name: /start progress/i }).click()
  await expect(page.getByText('In Progress').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /^Administrator$/ })).toBeVisible()

  // 담당자 해제. PATCH 본문이 {changes:{...}} 가 아니면 200 을 받고도
  // 아무것도 안 바뀐다 — 그 회귀를 여기서 잡는다.
  await page.getByRole('button', { name: /^Administrator$/ }).click()
  await page.getByRole('button', { name: /^unassigned$/i }).first().click()
  await expect(page.getByRole('button', { name: /^unassigned$/i })).toBeVisible()

  // 코멘트
  await page.getByLabel(/^comments$/i).fill('first comment')
  await page.getByRole('button', { name: /^comment$/i }).click()
  await expect(page.getByText('first comment')).toBeVisible()

  // 이력
  await expect(page.getByText(/changed status/i).first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('제목과 설명을 편집하면 실제로 저장된다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'before edit')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^summary$/i).fill('after edit')
  await writeBody(page, 'body text')
  await page.getByRole('button', { name: /^save$/i }).click()

  await expect(page.getByRole('heading', { name: 'after edit' })).toBeVisible()
  await expect(page.getByText('body text')).toBeVisible()

  // 새로고침해도 남아 있어야 한다. 화면 상태만 바뀌고 저장이 안 되는
  // 경우를 잡는다.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'after edit' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('필터 칩과 IQL 이 같은 결과를 낸다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'todo issue')

  await page.goto('/issues')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
  await expect(page.locator('tbody tr')).toHaveCount(1)

  // Done 칩은 아무것도 안 걸러야 한다 (이슈는 To do 상태다).
  await page.getByRole('button', { name: /^done$/i }).click()
  await expect(page.locator('tbody tr')).toHaveCount(0)

  await page.getByRole('button', { name: /^done$/i }).click()
  await page.getByRole('button', { name: /^to do$/i }).click()
  await expect(page.locator('tbody tr')).toHaveCount(1)

  // 칩이 만든 질의가 그대로 IQL 로 넘어가야 한다.
  await page.getByRole('button', { name: /edit as iql/i }).click()
  const iql = page.getByLabel('IQL')
  await expect(iql).toHaveValue(`project = "${key}" AND statusCategory = "todo"`)

  // 손으로 고친 질의는 칩으로 못 돌아간다 — 돌아가면 조용히 사라진다.
  await iql.fill(`project = "${key}" AND priority <= 5`)
  await page.getByRole('button', { name: /^run$/i }).click()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('button', { name: /back to filters/i })).toBeDisabled()

  expect(consoleErrors).toEqual([])
})

test('잘못된 IQL 은 오류를 보여준다', async ({ page }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  await page.goto('/issues')
  await page.getByRole('button', { name: /edit as iql/i }).click()
  await page.getByLabel('IQL').fill('status ==== broken')
  await page.getByRole('button', { name: /^run$/i }).click()

  await expect(page.getByRole('alert').first()).toContainText(/syntax/i)
})

test('표시 컬럼 선택이 새로고침 후에도 남는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/issues')

  const dueChip = page.getByRole('button', { name: /^due$/i })
  await expect(dueChip).toHaveAttribute('aria-pressed', 'false')
  await dueChip.click()
  await expect(dueChip).toHaveAttribute('aria-pressed', 'true')

  await page.reload()
  await expect(page.getByRole('button', { name: /^due$/i })).toHaveAttribute(
    'aria-pressed',
    'true',
  )

  expect(consoleErrors).toEqual([])
})

test('시간을 기록하면 추정 대비 실적이 보인다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'timed issue')

  // 추정을 먼저 잡는다.
  await page.getByRole('button', { name: /no estimate set/i }).click()
  await page.getByLabel(/set estimate/i).fill('2h')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: '2h' })).toBeVisible()

  // "1h 30m" 이 90분으로 읽혔다는 걸 저장 전에 보여줘야 한다.
  await page.getByLabel(/time spent/i).fill('1h 30m')
  await expect(page.getByText('= 1h 30m (90m)')).toBeVisible()
  await page.getByRole('button', { name: /^log$/i }).click()

  await expect(page.getByText('1h 30m').first()).toBeVisible()
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '90')

  // 넘기면 초과 표시.
  await page.getByLabel(/time spent/i).fill('1h')
  await page.getByRole('button', { name: /^log$/i }).click()
  await expect(page.getByText(/over estimate by 30m/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('읽을 수 없는 기간은 저장을 막는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'bad duration')

  await page.getByLabel(/time spent/i).fill('two hours')
  await expect(page.getByText(/can't read that as a duration/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /^log$/i })).toBeDisabled()

  expect(consoleErrors).toEqual([])
})

test('목록을 우선순위로 묶는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'grouped urgent', { priority: '1' })
  await createIssue(page, key, 'grouped lazy', { priority: '5' })

  await page.goto(`/issues?project=${key}`)
  await expect(page.locator('tbody tr')).toHaveCount(2)

  // "묶기" 줄의 우선순위 칩. 필터 칩에도 같은 이름이 있어 마지막 것을 쓴다.
  await page.getByRole('button', { name: /^priority$/i }).last().click()

  const headers = page.locator('th[scope="colgroup"]')
  await expect(headers).toHaveCount(2)
  // 1 이 가장 높다. 급한 것이 위로 온다.
  await expect(headers.first()).toContainText('Highest')
  await expect(headers.last()).toContainText('Lowest')

  // 묶어도 행이 사라지지 않는다.
  await expect(page.getByRole('cell', { name: 'grouped urgent' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'grouped lazy' })).toBeVisible()

  // 끄면 머리글이 사라진다.
  await page.getByRole('button', { name: /^none$/i }).last().click()
  await expect(headers).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})
