/**
 * 서식(WYSIWYG) 모드.
 *
 * 정본은 마크다운이다(ADR-0008). 여기서 확인할 것은 하나다 — **편집기를
 * 거쳤다는 이유로 문서가 달라지지 않는가**. 코퍼스 단위 왕복은 유닛 테스트가
 * 보지만(`doc.test.ts`), 실제 편집기 위에서 도는지는 브라우저 없이 알 수 없다.
 */

import { bodyField, createSpace, expect, signIn, test, uniqueKey, writeBody } from './fixtures'

function spaceKey(): string {
  return uniqueKey('Y')
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
  await writeBody(page, source)

  await page.getByRole('tab', { name: /rich text/i }).click()
  // 서식 모드에서 실제로 서식으로 보인다 — 원문이 그대로 보이면 안 된다.
  await expect(page.locator(`${RICH} h1`)).toHaveText('제목')
  await expect(page.locator(`${RICH} table td`).first()).toHaveText('1')

  await page.getByRole('tab', { name: /^write$/i }).click()
  // 목록 마커나 표 여백은 달라질 수 있어도 뜻은 그대로여야 한다.
  const back = await (await bodyField(page)).inputValue()
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
  const source = await (await bodyField(page)).inputValue()
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

test('서식 모드에서도 @ 로 사람을 넣는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Mentioned')

  const rich = page.locator(RICH)
  await rich.click()
  await page.keyboard.type('담당은 @Admin')

  const list = page.getByRole('listbox', { name: /people to mention/i })
  await expect(list).toBeVisible()
  // 키보드만으로 끝나야 한다 (ux-principles 3절).
  await page.keyboard.press('Enter')
  await expect(rich).toContainText('@Administrator')

  // 저장되는 것은 이름이 아니라 id 다. 이름이 바뀌어도 안 깨진다.
  const source = await (await bodyField(page)).inputValue()
  expect(source).toMatch(/담당은 \[@Administrator\]\(user:[0-9a-f-]+\)/)

  expect(consoleErrors).toEqual([])
})

test('서식 모드에 이슈 주소를 붙이면 이슈 링크가 된다', async ({ page, context, consoleErrors }) => {
  const key = spaceKey()
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Pasted link')

  const rich = page.locator(RICH)
  await rich.click()
  await page.keyboard.type('참고: ')
  await page.evaluate(() => navigator.clipboard.writeText('http://localhost:5173/issues/DEV-1'))
  await page.keyboard.press('Control+v')

  // 호스트를 그대로 저장하면 주소가 바뀌는 순간 전부 죽는다.
  const source = await (await bodyField(page)).inputValue()
  expect(source).toBe('참고: [DEV-1](issue:DEV-1)')

  expect(consoleErrors).toEqual([])
})

/**
 * 클립보드에 서식 있는 HTML 을 담아 붙여넣는다.
 *
 * `navigator.clipboard.write` 로는 임의의 `text/html` 을 못 넣는다(브라우저가
 * 막는다). 붙여넣기 이벤트를 직접 만들어 던진다 — 편집기가 보는 것은 같다.
 */
async function pasteHtml(
  page: import('@playwright/test').Page,
  selector: string,
  html: string,
  plain: string,
): Promise<boolean> {
  await page.locator(selector).click()
  // 우리가 가로챘는지는 `preventDefault` 로 드러난다. 만들어 던진 이벤트는
  // 믿을 수 있는 이벤트가 아니라서, 안 가로채도 브라우저가 대신 넣어 주지는
  // 않는다 — 그러니 "안 넣어졌다" 로는 아무것도 못 가린다.
  return page.locator(selector).evaluate(
    (element, [markup, text]) => {
      const data = new DataTransfer()
      data.setData('text/html', markup as string)
      data.setData('text/plain', text as string)
      const event = new ClipboardEvent('paste', {
        clipboardData: data,
        bubbles: true,
        cancelable: true,
      })
      element.dispatchEvent(event)
      return event.defaultPrevented
    },
    [html, plain],
  )
}

const CONFLUENCE = [
  '<h2>배포 절차</h2>',
  '<p>먼저 <strong>마이그레이션</strong>을 <em>검토</em>한다.</p>',
  '<ul><li>스테이징 확인</li><li>롤백 계획</li></ul>',
  '<table><thead><tr><th>단계</th><th>담당</th></tr></thead>',
  '<tbody><tr><td>빌드</td><td>CI</td></tr></tbody></table>',
  '<p><a href="https://runbook.example/deploy">런북</a></p>',
].join('')

test('서식 있는 HTML 을 붙여넣으면 마크다운이 된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Pasted html')

  // 평문만 떨어뜨리면 제목·표·링크가 통째로 사라진다.
  await pasteHtml(page, RICH, CONFLUENCE, '배포 절차 먼저 마이그레이션을 검토한다.')

  await expect(page.locator(`${RICH} h2`)).toHaveText('배포 절차')
  await expect(page.locator(`${RICH} table td`).first()).toHaveText('빌드')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toContain('## 배포 절차')
  expect(source).toContain('먼저 **마이그레이션**을 *검토*한다.')
  expect(source).toContain('- 스테이징 확인\n- 롤백 계획')
  expect(source).toContain('| 단계 | 담당 |')
  expect(source).toContain('[런북](https://runbook.example/deploy)')

  expect(consoleErrors).toEqual([])
})

test('소스 모드에 붙여넣어도 같은 마크다운이 된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Pasted into source')

  // 두 모드가 같은 클립보드에서 같은 문서를 만들어야 한다.
  await bodyField(page)
  await pasteHtml(page, '[role="tabpanel"] textarea', CONFLUENCE, '배포 절차')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toContain('## 배포 절차')
  expect(source).toContain('| 단계 | 담당 |')
  expect(source).toContain('[런북](https://runbook.example/deploy)')

  expect(consoleErrors).toEqual([])
})

test('색깔만 입힌 코드는 손대지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Pasted code')

  // 편집기에서 코드를 복사하면 이렇게 온다. 변환하면 줄이 문단으로 흩어지고
  // 들여쓰기가 사라진다. 손대지 않고 브라우저가 하던 대로 두어야 한다.
  const html = '<div><span style="color:#001080">if</span> (a) {</div><div>  b()</div><div>}</div>'
  await bodyField(page)
  const handled = await pasteHtml(page, '[role="tabpanel"] textarea', html, 'if (a) {\n  b()\n}')
  expect(handled).toBe(false)

  // 서식 모드도 같은 판단을 해야 한다 — 모드에 따라 결과가 갈리면 안 된다.
  // 여기서는 편집기가 자기 식으로 읽어 버리므로 우리가 평문으로 넣는다.
  await page.getByRole('tab', { name: /rich text/i }).click()
  await pasteHtml(page, RICH, html, 'if (a) {\n  b()\n}')
  const rich = await (await bodyField(page)).inputValue()
  expect(rich).toContain('if (a) {')
  // 줄마다 문단이 되면 사이에 빈 줄이 낀다.
  expect(rich).not.toContain('{\n\n')

  expect(consoleErrors).toEqual([])
})

test('성근 목록은 서식 모드에서 고쳐도 성글다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'Loose list')

  // 마크다운에는 촘촘한 목록과 성근 목록이 따로 있다. 편집기 스키마에는
  // 없으므로 속성으로 들고 있어야 한다 — 안 그러면 글자 하나만 고쳐도
  // 항목 사이 빈 줄이 사라진다.
  await writeBody(page, '- 하나\n\n- 둘\n')
  await page.getByRole('tab', { name: /rich text/i }).click()
  const rich = page.locator(RICH)
  await rich.click()
  await page.keyboard.press('End')
  await page.keyboard.type('!')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toContain('- 하나\n\n- 둘')

  expect(consoleErrors).toEqual([])
})

test('구글 문서에서 가져온 글이 문서 모양 그대로 들어온다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await openEditor(page, key, 'From docs')

  // 구글 문서는 붙여넣기 전체를 `<b style="font-weight:normal">` 로 감싸고
  // 굵기·기울임을 span 스타일로 준다. 껍데기를 인라인으로 읽으면 제목도
  // 목록도 한 문단으로 뭉개진다.
  const html = [
    '<meta charset="utf-8"><b style="font-weight:normal" id="docs-internal-guid-1">',
    '<h1><span style="font-size:20pt;font-weight:700">릴리스 노트</span></h1>',
    '<p><span style="font-weight:400">이번 판에서 </span>',
    '<span style="font-weight:700">검색</span>',
    '<span style="font-weight:400">이 </span>',
    '<span style="font-style:italic">훨씬</span>',
    '<span style="font-weight:400"> 빨라졌다.</span></p>',
    '<ul><li><p><span>색인을 다시 만들었다</span></p></li>',
    '<li><p><span>느린 질의를 고쳤다</span></p></li></ul></b>',
  ].join('')
  await page.getByRole('tab', { name: /rich text/i }).click()
  await pasteHtml(page, RICH, html, '릴리스 노트')

  const source = await (await bodyField(page)).inputValue()
  expect(source).toBe(
    [
      '# 릴리스 노트',
      '',
      '이번 판에서 **검색**이 *훨씬* 빨라졌다.',
      '',
      '- 색인을 다시 만들었다',
      '- 느린 질의를 고쳤다',
    ].join('\n'),
  )

  expect(consoleErrors).toEqual([])
})
