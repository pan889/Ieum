import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

test('선택한 이슈를 한 번에 바꾼다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'bulk one')
  await createIssue(page, key, 'bulk two')

  await page.goto('/issues')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
  await expect(page.locator('tbody tr')).toHaveCount(2)

  await page.getByLabel(/select all on this page/i).check()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByLabel(/set priority/i).selectOption({ label: 'Highest' })
  await page.getByLabel(/add labels/i).fill('urgent, triaged')
  await page.getByRole('button', { name: /^apply$/i }).click()

  await expect(page.getByText('2 issues updated')).toBeVisible()

  // 실제로 바뀌었는지 목록에서 확인한다.
  await page.getByRole('button', { name: /^highest$/i }).click()
  await expect(page.locator('tbody tr')).toHaveCount(2)

  expect(consoleErrors).toEqual([])
})

test('CSV 를 내보낸다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'exported issue')

  await page.goto('/issues')
  await page.getByLabel(/^project$/i).selectOption({ label: `${key} · E2E` })
  await expect(page.locator('tbody tr')).toHaveCount(1)

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: /export csv/i }).click(),
  ])
  const stream = await download.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(chunk as Buffer)
  const text = Buffer.concat(chunks).toString('utf8')

  // BOM 이 없으면 엑셀이 한국어를 깨뜨린다.
  expect(text.charCodeAt(0)).toBe(0xfeff)
  expect(text).toContain('key,summary')
  expect(text).toContain('exported issue')
  expect(text).toContain(`${key}-1`)

  expect(consoleErrors).toEqual([])
})

test('범위를 좁히지 않으면 내보내기가 막혀 있다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/issues')
  // 필터가 비면 질의도 빈 문자열이다 — 설치 전체를 한 파일로 뽑게 된다.
  await expect(page.getByRole('button', { name: /export csv/i })).toBeDisabled()
  expect(consoleErrors).toEqual([])
})
