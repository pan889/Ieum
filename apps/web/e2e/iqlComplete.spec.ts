/**
 * IQL 자동완성.
 *
 * 완성된 질의가 **실제로 실행되는지**가 핵심이다. 제안 문자열만 검사하면
 * "그럴듯한 후보를 냈지만 끼우고 나면 서버가 거절하는" 경우를 놓친다.
 * 키보드 조작(↑↓·Enter·Tab·Esc)과 커서 이동은 브라우저 없이 볼 수 없다.
 */

import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

async function openIql(page: import('@playwright/test').Page) {
  await page.goto('/issues')
  await page.getByRole('button', { name: /edit as iql/i }).click()
  const box = page.getByRole('combobox', { name: 'IQL' })
  await box.click()
  return box
}

function options(page: import('@playwright/test').Page) {
  return page.getByRole('listbox', { name: /suggestions/i }).getByRole('option')
}

test('필드 → 연산자 → 값을 키보드로만 완성한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'completed by keyboard')

  const box = await openIql(page)
  await box.fill('pro')
  // 친 글자로 좁혀진다.
  await expect(options(page).first()).toHaveText(/progress/)

  await box.press('ArrowDown')
  await box.press('Enter')
  // 고르면 커서가 다음 자리로 가고, 그 자리의 후보가 곧바로 뜬다.
  await expect(box).toHaveValue('project ')
  await expect(options(page).first()).toHaveText(/^=/)

  await box.press('Enter')
  await expect(box).toHaveValue('project = ')

  // 값은 서버가 준다. 목록은 여덟 개까지만 오므로 쳐서 좁힌다 — 훑는
  // 목록이 아니라 찾는 목록이다 (ux-principles 4절).
  await box.pressSequentially(key.slice(0, 3))
  await page.getByRole('option', { name: new RegExp(key) }).click()
  await expect(box).toHaveValue(`project = "${key}" `)

  await box.press('Control+Enter')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'completed by keyboard' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('Tab 으로도 넣고 Esc 로 닫는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  const box = await openIql(page)

  await box.fill('assign')
  await expect(options(page).first()).toBeVisible()
  await box.press('Tab')
  await expect(box).toHaveValue('assignee ')

  await box.press('Escape')
  await expect(options(page)).toHaveCount(0)

  // 닫아 둔 목록을 다시 부른다.
  await box.press('Control+ ')
  await expect(options(page).first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('필드 종류에 맞지 않는 연산자는 내놓지 않는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  const box = await openIql(page)

  // bool 필드에 `>` 를 제안하면 고르는 순간 오류가 난다.
  await box.fill('archived ')
  await box.press('Control+ ')
  await expect(options(page)).toHaveText(['=operator', '!=operator'])

  expect(consoleErrors).toEqual([])
})

test('담당자는 이름으로 고르고 ID 가 들어간다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'assigned to nobody')

  const box = await openIql(page)
  await box.fill('assignee = Admin')
  await box.press('Control+ ')

  // 사용자 값은 UUID 로 컴파일된다. 손으로 칠 수 있는 값이 아니다.
  await page.getByRole('option', { name: /Administrator/ }).click()
  await expect(box).toHaveValue(/^assignee = "[0-9a-f-]{36}" $/)

  await box.press('Control+Enter')
  // 실행된다 — 목록이 비더라도 오류 배너는 없어야 한다.
  await expect(page.getByRole('alert')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('깨진 질의에는 아무것도 제안하지 않는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  const box = await openIql(page)

  await box.fill('project = = =')
  await box.press('Control+ ')
  // 틀린 제안은 없는 것만 못하다.
  await expect(options(page)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})
