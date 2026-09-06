import { bodyField, createIssue, createProject, expect, projectKey, signIn, test, writeBody } from './fixtures'

test('설명과 코멘트를 마크다운으로 그린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'markdown issue')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, 
    ['## Heading', '', '- [x] done', '- [ ] todo', '', '| a | b |', '| - | - |', '| 1 | 2 |'].join(
      '\n',
    ),
  )
  await page.getByRole('button', { name: /^save$/i }).click()

  const body = page.locator('.ieum-markdown').first()
  await expect(body.locator('h2')).toHaveText('Heading')
  await expect(body.locator('input[type=checkbox]')).toHaveCount(2)
  await expect(body.locator('table td').first()).toHaveText('1')

  expect(consoleErrors).toEqual([])
})

test('본문에 넣은 스크립트는 실행되지 않는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'xss issue')

  // 실행되면 여기 기록된다. 방언이 원시 HTML 을 끄고 있어야 비어 있다.
  await page.addInitScript(() => {
    ;(window as unknown as { __xss: string[] }).__xss = []
  })
  await page.reload()

  const payload = [
    '<script>window.__xss.push("script")</script>',
    '<img src=x onerror="window.__xss.push(\'img\')">',
    '<svg onload="window.__xss.push(\'svg\')"></svg>',
    '[click](javascript:window.__xss.push("link"))',
  ].join('\n\n')

  await page.getByLabel(/^comments$/i).fill(payload)
  await page.getByRole('button', { name: /^comment$/i }).click()
  await expect(page.locator('.ieum-markdown').last()).toBeVisible()

  // 링크가 만들어졌다면 눌러 본다 — 안 만들어지는 게 정상이다.
  const link = page.getByRole('link', { name: 'click' })
  if ((await link.count()) > 0) await link.first().click()

  const fired = await page.evaluate(() => (window as unknown as { __xss: string[] }).__xss)
  expect(fired).toEqual([])
  await expect(page.locator('script:has-text("__xss")')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('저장할 때 마크다운이 정규화된다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'normalize issue')

  await page.getByRole('button', { name: /^edit$/i }).click()
  // setext 제목과 * 목록은 정규화되면 # 과 - 로 바뀐다.
  await writeBody(page, 'Title\n=====\n\n*  a\n*  b\n')
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.locator('.ieum-markdown h1')).toHaveText('Title')

  // 다시 편집 폼을 열면 정규화된 원문이 들어 있어야 한다.
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(await bodyField(page)).toHaveValue('# Title\n\n- a\n- b')

  expect(consoleErrors).toEqual([])
})

test('미리보기 탭이 입력한 마크다운을 그린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'preview issue')

  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '## Preview me\n\n- one\n- two\n')

  await page.getByRole('tab', { name: /^preview$/i }).first().click()
  const preview = page.getByRole('tabpanel').first()
  await expect(preview.locator('h2')).toHaveText('Preview me')
  await expect(preview.locator('li')).toHaveCount(2)

  // 작성 탭으로 돌아가도 입력이 남아 있어야 한다.
  await page.getByRole('tab', { name: /^write$/i }).first().click()
  await expect(await bodyField(page)).toHaveValue('## Preview me\n\n- one\n- two\n')

  expect(consoleErrors).toEqual([])
})
