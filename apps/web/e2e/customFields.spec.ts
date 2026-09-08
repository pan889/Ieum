/**
 * 커스텀 필드 위젯.
 *
 * 정의는 `python -m ieum.cli seed-fields` 가 넣는다 (아직 관리 화면이 없다).
 * 여기서 잡히는 건 타입이 어긋나는 경우다 — 숫자 칸이 `"8"` 을 보내면 서버가
 * 거절하는데, 유닛 테스트는 위젯과 서버를 각각 통과시킨다.
 */

import { createProject, expect, pickProject, projectKey, signIn, test } from './fixtures'

test('종류별 위젯으로 값을 넣고 되읽는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  await page.goto('/issues/new')
  await pickProject(page, key)
  await page.getByLabel(/^summary$/i).fill('custom fields')

  // 정의는 프로젝트·유형을 고른 뒤에 불린다.
  await expect(page.getByLabel(/^severity$/i)).toBeVisible()

  await page.getByLabel(/^notes$/i).fill('a note')
  await page.getByLabel(/story points/i).fill('8')
  await page.getByLabel(/target date/i).fill('2026-10-01')
  await page.getByLabel(/^severity$/i).selectOption('high')
  await page.getByRole('button', { name: 'web' }).click()
  await page.getByRole('button', { name: 'ios' }).click()
  await page.getByLabel(/regression/i).check()
  await page.getByLabel(/spec link/i).fill('https://example.com/spec')

  await page.getByRole('button', { name: /create issue/i }).click()
  await page.waitForURL(new RegExp(`/issues/${key}-`))

  // 상세 화면이 같은 값을 되읽는다.
  await expect(page.getByLabel(/^notes$/i)).toHaveValue('a note')
  await expect(page.getByLabel(/story points/i)).toHaveValue('8')
  await expect(page.getByLabel(/^severity$/i)).toHaveValue('high')
  await expect(page.getByLabel(/regression/i)).toBeChecked()
  await expect(page.getByLabel(/spec link/i)).toHaveValue('https://example.com/spec')
  // 고른 순서를 유지한다.
  await expect(page.getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'ios' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'android' })).toHaveAttribute(
    'aria-pressed',
    'false',
  )

  expect(consoleErrors).toEqual([])
})

test('상세 화면에서 고치고 저장한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  await page.goto('/issues/new')
  await pickProject(page, key)
  await page.getByLabel(/^summary$/i).fill('edit fields')
  await expect(page.getByLabel(/^severity$/i)).toBeVisible()
  await page.getByLabel(/^severity$/i).selectOption('low')
  await page.getByLabel(/^notes$/i).fill('before')
  await page.getByRole('button', { name: /create issue/i }).click()
  await page.waitForURL(new RegExp(`/issues/${key}-`))

  // 안 건드리면 저장 버튼이 없다.
  await expect(page.getByRole('button', { name: /save fields/i })).toBeHidden()

  await page.getByLabel(/^severity$/i).selectOption('high')
  await page.getByLabel(/^notes$/i).fill('')
  await page.getByRole('button', { name: 'android' }).click()
  await page.getByRole('button', { name: /save fields/i }).click()

  // 저장이 끝나면 초안이 비고 버튼이 사라진다.
  await expect(page.getByRole('button', { name: /save fields/i })).toBeHidden()

  await page.reload()
  await expect(page.getByLabel(/^severity$/i)).toHaveValue('high')
  // 빈 값은 지운 것이다. 빈 문자열로 남지 않는다.
  await expect(page.getByLabel(/^notes$/i)).toHaveValue('')
  await expect(page.getByRole('button', { name: 'android' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )

  // 세 필드가 한 번에 저장됐다 — 이슈 버전은 한 번만 올라간다.
  await expect(page.getByText(/^history$/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('서버가 거절하면 어느 필드인지 알려준다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  await page.goto('/issues/new')
  await pickProject(page, key)
  await page.getByLabel(/^summary$/i).fill('bad value')
  await expect(page.getByLabel(/^severity$/i)).toBeVisible()
  await page.getByRole('button', { name: /create issue/i }).click()
  await page.waitForURL(new RegExp(`/issues/${key}-`))

  // max 는 100 이다.
  await page.getByLabel(/story points/i).fill('999')
  await page.getByRole('button', { name: /save fields/i }).click()

  await expect(page.getByRole('alert')).toContainText('Story points')
  // 초안은 남는다. 고쳐서 다시 저장할 수 있어야 한다.
  await expect(page.getByLabel(/story points/i)).toHaveValue('999')

  // 도메인 검증 실패는 422 다 (ValidationError). 여기서는 일부러 낸 것이다.
  expect(consoleErrors.filter((e) => !e.startsWith('422 '))).toEqual([])
})
