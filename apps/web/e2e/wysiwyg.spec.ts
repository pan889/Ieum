/**
 * 서식(WYSIWYG) 모드.
 *
 * 정본은 마크다운이다(ADR-0008). 여기서 확인할 것은 하나다 — **편집기를
 * 거쳤다는 이유로 문서가 달라지지 않는가**. 코퍼스 단위 왕복은 유닛 테스트가
 * 보지만(`doc.test.ts`), 실제 편집기 위에서 도는지는 브라우저 없이 알 수 없다.
 */

import { createSpace, expect, signIn, test } from './fixtures'

function spaceKey(): string {
  return 'Y' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

async function openEditor(page: import('@playwright/test').Page, key: string, title: string) {
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
}

const RICH = '[role="tabpanel"] [contenteditable="true"]'

test('소스로 쓴 문서를 서식으로 열었다 돌아와도 그대로다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Round trip')

  const source = [
    '# 제목',
    '',
    '- 하나',
    '- 둘',
    '',
    '| a | b |',
    '| --- | --- |',
    '| 1 | 2 |',
    '',
    ':::info',
    '지켜야 할 것',
    ':::',
  ].join('\n')
  await page.getByLabel(/^body$/i).fill(source)

  await page.getByRole('tab', { name: /rich text/i }).click()
  // 서식 모드에서 실제로 서식으로 보인다 — 원문이 그대로 보이면 안 된다.
  await expect(page.locator(`${RICH} h1`)).toHaveText('제목')
  await expect(page.locator(`${RICH} table td`).first()).toHaveText('1')

  await page.getByRole('tab', { name: /^write$/i }).click()
  // 목록 마커나 표 여백은 달라질 수 있어도 뜻은 그대로여야 한다.
  const back = await page.getByLabel(/^body$/i).inputValue()
  expect(back).toContain('# 제목')
  expect(back).toContain('- 하나')
  expect(back).toContain('| 1 | 2 |')
  // 편집기가 다루지 않는 블록은 원문 그대로 남는다. 이게 안 되면 서식 모드를
  // 한 번 열어 본 것만으로 디렉티브가 사라진다.
  expect(back).toContain(':::info\n지켜야 할 것\n:::')

  expect(consoleErrors).toEqual([])
})

test('마크다운 단축이 그대로 서식이 된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Shortcuts')

  await page.getByRole('tab', { name: /rich text/i }).click()
  const rich = page.locator(RICH)
  await rich.click()
  await page.keyboard.type('## 새 절\n')
  await page.keyboard.type('보통 글에 **굵게** 와 `코드`.\n')
  await page.keyboard.type('- 하나\n')
  await page.keyboard.type('둘')

  await expect(page.locator(`${RICH} h2`)).toHaveText('새 절')
  await expect(page.locator(`${RICH} strong`)).toHaveText('굵게')
  await expect(page.locator(`${RICH} li`)).toHaveCount(2)

  await page.getByRole('tab', { name: /^write$/i }).click()
  const source = await page.getByLabel(/^body$/i).inputValue()
  expect(source).toContain('## 새 절')
  expect(source).toContain('**굵게**')
  expect(source).toContain('- 하나\n- 둘')

  expect(consoleErrors).toEqual([])
})

test('서식으로 쓴 글이 문서로 저장된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Saved from rich')

  await page.getByRole('tab', { name: /rich text/i }).click()
  const rich = page.locator(RICH)
  await rich.click()
  await page.keyboard.type('# 배포 절차\n')
  await page.keyboard.type('1. 빌드\n')
  await page.keyboard.type('배포\n')

  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('heading', { name: '배포 절차' })).toBeVisible()
  await expect(page.getByRole('listitem').filter({ hasText: '빌드' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})
