import { createIssue, createProject, expect, projectKey, signIn, test } from './fixtures'

test('파일을 붙이면 목록에 뜨고 내려받을 수 있다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'attach issue')

  await expect(page.getByText(/no files attached/i)).toBeVisible()

  // 브라우저가 스토리지로 **직접** 올린다. 서버는 바이트를 안 거친다.
  await page.getByLabel(/attach files/i).setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('hello from the browser'),
  })

  const link = page.getByRole('button', { name: 'notes.txt' })
  await expect(link).toBeVisible()
  await expect(page.getByText('22 B')).toBeVisible()

  // 클릭하면 인증된 요청으로 presigned URL 을 받아 새 탭에서 연다.
  // 앵커에 서버 경로를 박아 두면 Authorization 헤더가 안 붙어 401 이 난다.
  // 탭이 열리는 것 자체가 검증 대상이다 — 응답을 기다렸다 열면 팝업 차단기가
  // 조용히 막아 사용자는 눌렀는데 아무 일도 안 일어난 것처럼 보인다.
  // text/plain 은 Content-Disposition: attachment 로 내려온다. 크로미움이
  // 다운로드를 여는 탭에 붙일지 원래 탭에 붙일지는 상황에 따라 다르므로 둘 다 본다.
  const fromOpener = page.waitForEvent('download')
  const [popup] = await Promise.all([page.waitForEvent('popup'), link.click()])
  const downloaded = await Promise.race([fromOpener, popup.waitForEvent('download')])

  expect(downloaded.suggestedFilename()).toBe('notes.txt')
  const stream = await downloaded.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(chunk as Buffer)
  expect(Buffer.concat(chunks).toString()).toBe('hello from the browser')

  expect(consoleErrors).toEqual([])
})

test('실행 가능한 형식은 거절한다', async ({ page }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'blocked upload')

  // text/html 을 우리 오리진에서 열면 스크립트가 도는 것과 같다.
  await page.getByLabel(/attach files/i).setInputFiles({
    name: 'evil.html',
    mimeType: 'text/html',
    buffer: Buffer.from('<script>alert(1)</script>'),
  })

  await expect(page.getByRole('alert')).toContainText(/can't be uploaded/i)
  await expect(page.getByRole('button', { name: 'evil.html' })).toHaveCount(0)
})

test('첨부를 지우면 목록에서 사라진다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'delete attachment')

  await page.getByLabel(/attach files/i).setInputFiles({
    name: 'temp.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('bye'),
  })
  await expect(page.getByRole('button', { name: 'temp.txt' })).toBeVisible()

  await page.getByRole('button', { name: /remove attachment/i }).click()
  await expect(page.getByText(/no files attached/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
