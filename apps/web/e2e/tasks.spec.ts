/**
 * 태스크 리스트 — 체크, 담당자·기한, 내 할 일 (feature-map B12).
 *
 * 파서는 서버 시험 27개가 값으로, 서비스는 19개가 권한과 저장 경로로 붙잡고,
 * 날짜 계산은 프론트 단위 14개가 붙잡는다. 브라우저로 보는 것은 **사슬이
 * 실제로 이어졌는가**다:
 *
 * - 본문에 `- [ ]` 를 쓰면 목록에 뜬다.
 * - **체크하면 본문이 바뀐다.** 유도 표만 바뀌고 본문이 그대로면 다음 저장에
 *   되돌아가고, 그 사이에는 아무 표시도 없다.
 * - 기한이 지난 것은 지났다고 보인다.
 * - **내 할 일이 문서 넘어 모인다.** 못 보는 문서의 것은 안 뜬다.
 */

import type { Page } from '@playwright/test'

import { createSpace, expect, signIn, test, uniqueKey, writeBody } from './fixtures'

test.slow()

function spaceKey(): string {
  return uniqueKey('K')
}

async function newPage(page: Page, key: string, title: string, body: string): Promise<void> {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
}

test('본문의 체크리스트가 태스크 목록으로 뜬다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Runbook', '# 배포\n\n- [ ] 빌드 확인\n- [x] 릴리스 노트\n')

  const panel = page.getByTestId('task-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('빌드 확인')
  await expect(panel).toContainText('릴리스 노트')
  // 하나만 남았다 — 서버가 센 값이다.
  await expect(panel).toContainText(/1 of 2 open/i)

  expect(consoleErrors).toEqual([])
})

test('태스크가 없는 문서에는 상자가 안 생긴다', async ({ page, consoleErrors }) => {
  // 빈 상자를 두면 모든 문서에 쓸모 없는 칸이 하나 생긴다.
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Plain', '# 그냥 문서\n\n체크리스트가 없다.\n')

  await expect(page.getByTestId('task-panel')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('체크하면 본문이 바뀐다', async ({ page, consoleErrors }) => {
  /**
   * **이 시험이 이 파일의 이유다.**
   *
   * 유도 표만 바꾸고 본문을 안 고치면 화면은 체크된 것처럼 보이고, 다음
   * 저장에서 조용히 되돌아간다. 본문이 정본이므로(ADR-0008) 본문이 바뀐
   * 것을 봐야 한다 — 소스 모드를 열어 확인한다.
   */
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Checklist', '- [ ] 눌러 볼 일\n')

  const panel = page.getByTestId('task-panel')
  // `check()` 는 못 쓴다: 이 체크박스는 서버가 새 판을 만든 뒤에야 켜지므로
  // 누른 직후의 상태를 확인하는 그 헬퍼는 실패한다. 낙관적으로 먼저 켜면
  // 거절당했을 때(줄이 밀렸다/글이 바뀌었다) 되돌아가는데, 그건 "끝냈다고
  // 표시했다" 를 잠깐 거짓으로 만든다 — 그래서 안 켠다.
  const box = panel.getByRole('checkbox').first()
  await box.click()
  await expect(box).toBeChecked()
  await expect(panel).toContainText(/0 of 1 open/i)

  // 원문을 본다. 마커가 실제로 바뀌어 있어야 한다.
  await page.getByRole('button', { name: /^edit$/i }).click()
  const write = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await write.getAttribute('aria-selected')) !== 'true') await write.click()
  await expect(page.locator('[role="tabpanel"] textarea').first()).toHaveValue(
    /- \[x\] 눌러 볼 일/,
  )

  expect(consoleErrors).toEqual([])
})

test('지난 기한은 지났다고 보인다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Overdue', '- [ ] 늦은 일 due:2020-01-01\n')

  const panel = page.getByTestId('task-panel')
  await expect(panel).toContainText(/overdue/i)
  // 기한 표기는 글에서 빠진다 — 옆에 날짜가 따로 있으므로 군더더기다.
  await expect(panel).not.toContainText('due:2020-01-01')

  expect(consoleErrors).toEqual([])
})

test('내게 걸린 일이 문서 넘어 모인다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  // 자기 자신을 멘션한다. 멘션 자동완성으로 넣어야 `user:` 링크가 된다 —
  // 손으로 `@이름` 만 적으면 담당자가 아니다.
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Assigned')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  const write = page.getByRole('tab', { name: /^write$/i }).first()
  if ((await write.getAttribute('aria-selected')) !== 'true') await write.click()
  const box = page.locator('[role="tabpanel"] textarea').first()
  await box.click()
  await box.pressSequentially('- [ ] 내가 할 일 @')
  // 멘션 후보에서 고른다 — 손으로 이름만 적으면 `user:` 링크가 아니다.
  const people = page.getByRole('listbox', { name: /people to mention/i })
  await expect(people).toBeVisible()
  await people.getByRole('option').first().click()
  await expect(box).toHaveValue(/\[@Administrator\]\(user:[0-9a-f-]+\)/)
  await box.pressSequentially(' due:2030-12-31')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()

  await page.goto('/wiki/tasks')
  await expect(page.getByRole('heading', { name: /my tasks/i, level: 1 })).toBeVisible()
  const list = page.getByTestId('my-tasks')
  await expect(list).toContainText('내가 할 일')
  // **어느 문서의 일인지 보인다.** 없으면 하나씩 눌러 확인해야 한다.
  await expect(list).toContainText('Assigned')
  await expect(list).toContainText(key)

  expect(consoleErrors).toEqual([])
})
