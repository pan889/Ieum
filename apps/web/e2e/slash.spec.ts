/**
 * `/` 삽입 명령 (ux-principles 6절).
 *
 * 표·코드는 마크다운 단축으로 칠 수 있지만 디렉티브는 아니다 —
 * `::children{depth=2}` 를 외우는 사람은 없다. 두 모드가 **같은 목록**에서
 * **같은 마크다운**을 넣는지가 여기서 볼 것이다.
 */

import { bodyField, createSpace, expect, signIn, test, uniqueKey } from './fixtures'

function spaceKey(): string {
  return uniqueKey('S')
}

const RICH = '[role="tabpanel"] [contenteditable="true"]'

async function openEditor(page: import('@playwright/test').Page, key: string, title: string) {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
}

function commandList(page: import('@playwright/test').Page) {
  return page.getByRole('listbox', { name: /things to insert/i })
}

test('서식 모드에서 / 로 표를 넣는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash table')

  await page.getByRole('tab', { name: /rich text/i }).click()
  await page.locator(RICH).click()
  await page.keyboard.type('/')
  await expect(commandList(page)).toBeVisible()

  await page.keyboard.type('tab')
  await expect(commandList(page).getByRole('option')).toHaveText(['Table'])
  // 키보드만으로 끝나야 한다 (ux-principles 3절).
  await page.keyboard.press('Enter')

  await expect(page.locator(`${RICH} table`)).toBeVisible()
  const source = await (await bodyField(page)).inputValue()
  // `/tab` 을 치던 빈 문단이 남으면 넣을 때마다 빈 줄이 하나씩 쌓인다.
  expect(source).toBe('|  |  |\n| --- | --- |\n|  |  |')

  expect(consoleErrors).toEqual([])
})

test('서식 모드에서 / 로 디렉티브를 넣는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash directive')

  await page.getByRole('tab', { name: /rich text/i }).click()
  await page.locator(RICH).click()
  await page.keyboard.type('/warn')
  await expect(commandList(page).getByRole('option')).toHaveText(['Warning box'])
  await page.keyboard.press('Enter')

  // 편집기가 다루지 않는 블록이므로 원문 그대로 남는다.
  const source = await (await bodyField(page)).inputValue()
  expect(source).toBe(':::warning\n\n:::')

  expect(consoleErrors).toEqual([])
})

test('소스 모드도 같은 목록에서 같은 것을 넣는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash source')

  const field = await bodyField(page)
  await field.fill('앞 문단\n')
  await field.click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type('/toc')
  await expect(commandList(page).getByRole('option')).toHaveText(['Table of contents'])
  await page.keyboard.press('Enter')

  // 앞에 글이 있으면 빈 줄을 넣는다 — 안 그러면 앞 문단에 이어 붙는다.
  await expect(field).toHaveValue('앞 문단\n\n::toc\n\n')

  expect(consoleErrors).toEqual([])
})

test('경로를 치는 중에는 목록이 열리지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash path')

  const field = await bodyField(page)
  await field.click()
  // 아무 데서나 열면 경로나 날짜를 칠 때마다 목록이 튀어나온다.
  await page.keyboard.type('보라 src/shared')
  await expect(commandList(page)).toBeHidden()

  await page.getByRole('tab', { name: /rich text/i }).click()
  await page.locator(RICH).click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type(' 그리고 a/b')
  await expect(commandList(page)).toBeHidden()

  expect(consoleErrors).toEqual([])
})

test('목록이 열려 있으면 방향키가 커서를 움직이지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash keys')

  await page.getByRole('tab', { name: /rich text/i }).click()
  await page.locator(RICH).click()
  await page.keyboard.type('/head')
  const options = commandList(page).getByRole('option')
  await expect(options).toHaveText(['Heading 1', 'Heading 2', 'Heading 3'])
  await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true')

  await page.keyboard.press('ArrowDown')
  await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Enter')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toBe('##')

  expect(consoleErrors).toEqual([])
})

test('Escape 로 닫으면 친 글자가 남는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash escape')

  const field = await bodyField(page)
  await field.click()
  await page.keyboard.type('/tab')
  await expect(commandList(page)).toBeVisible()

  // 닫는다고 친 글자를 지우면 안 된다 — 그냥 `/tab` 이라고 쓰려던 것일 수 있다.
  await page.keyboard.press('Escape')
  await expect(commandList(page)).toBeHidden()
  await expect(field).toHaveValue('/tab')

  expect(consoleErrors).toEqual([])
})

test('멘션은 그대로 있고 명령과 섞이지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Slash and mention')

  await page.getByRole('tab', { name: /rich text/i }).click()
  await page.locator(RICH).click()
  await page.keyboard.type('담당 @Admin')
  await expect(page.getByRole('listbox', { name: /people to mention/i })).toBeVisible()
  await expect(commandList(page)).toBeHidden()
  await page.keyboard.press('Enter')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toMatch(/^담당 \[@Administrator\]\(user:[0-9a-f-]+\)$/)

  expect(consoleErrors).toEqual([])
})
