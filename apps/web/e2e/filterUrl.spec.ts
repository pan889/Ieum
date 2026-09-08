/**
 * 필터의 URL 공유와 저장 필터.
 *
 * 완료 조건이 "IQL 로 저장한 필터가 URL 로 공유된다" 이므로 링크를 열었을
 * 때 같은 목록이 나오는지가 핵심이다. 이건 브라우저 없이는 확인할 수 없다 —
 * 라우터·히스토리·역직렬화가 전부 얽혀 있다.
 */

import { createIssue, createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

test('칩이 URL 에 실리고 새로고침해도 남는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'url shared issue')

  await page.goto('/issues')
  await pickProject(page, key)
  await expect(page).toHaveURL(new RegExp(`project=${key}`))
  await expect(page.locator('tbody tr')).toHaveCount(1)

  await page.reload()
  // 새로고침해도 같은 목록이다. 필터가 컴포넌트 state 였다면 여기서 날아간다.
  // 피커는 URL 의 **키**로 프로젝트를 되찾아 이름까지 보여 준다.
  await expect(page.getByText(`${key} · E2E`)).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})

test('링크를 그대로 열면 같은 목록이 나온다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'link target')

  // 주소창에 직접 친 것과 같다.
  await page.goto(`/issues?project=${key}&status=todo`)
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'link target' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('망가진 링크도 목록을 보여 준다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'still visible')

  // 우선순위가 숫자가 아니다. 그대로 IQL 에 실리면 서버가 질의를 거절한다.
  await page.goto(`/issues?project=${key}&priority=high&status=nope`)
  await expect(page.locator('tbody tr')).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})

test('IQL 로 전환해도 칩으로 돌아갈 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'round trip')

  await page.goto(`/issues?project=${key}`)
  await page.getByRole('button', { name: /edit as iql/i }).click()
  await page.getByRole('button', { name: /^run$/i }).click()
  await expect(page).toHaveURL(/[?&]iql=/)

  // 손대지 않았으므로 되돌아갈 수 있다.
  const back = page.getByRole('button', { name: /back to filters/i })
  await expect(back).toBeEnabled()
  await back.click()
  await expect(page).not.toHaveURL(/[?&]iql=/)
  await expect(page.getByText(`${key} · E2E`)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('손으로 고친 질의는 칩으로 못 돌아간다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  await page.goto(`/issues?project=${key}`)
  await page.getByRole('button', { name: /edit as iql/i }).click()
  await page.getByLabel(/iql/i).first().fill(`project = "${key}" AND priority IN (1, 2)`)
  await page.getByRole('button', { name: /^run$/i }).click()

  // 되돌리면 사용자가 쓴 질의가 조용히 사라진다. 그래서 막는다.
  await expect(page.getByRole('button', { name: /back to filters/i })).toBeDisabled()

  expect(consoleErrors).toEqual([])
})

test('필터를 저장하고 다시 불러온다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  const name = `filter ${key}`
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'saved filter target')

  await page.goto(`/issues?project=${key}`)
  await expect(page.locator('tbody tr')).toHaveCount(1)

  await page.getByRole('button', { name: /save filter/i }).click()
  await page.getByLabel(/filter name/i).fill(name)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name, exact: true })).toBeVisible()

  // 필터를 비우고, 저장한 것을 눌러 되살린다.
  await page.goto('/issues')
  await page.getByRole('button', { name, exact: true }).click()
  await expect(page).toHaveURL(/[?&]iql=/)
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'saved filter target' })).toBeVisible()

  // 정리. 시드 DB 를 공유하므로 남기면 다음 실행의 이름과 부딪힐 수 있다.
  await page.getByRole('button', { name: `Delete ${name}` }).click()
  await expect(page.getByRole('button', { name, exact: true })).toBeHidden()

  expect(consoleErrors).toEqual([])
})

test('빈 질의는 저장할 수 없다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/issues')

  // 조건 없는 필터는 이름만 있는 "전체 이슈" 다.
  await expect(page.getByRole('button', { name: /save filter/i })).toBeDisabled()

  expect(consoleErrors).toEqual([])
})
