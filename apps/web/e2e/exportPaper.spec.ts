/**
 * 인쇄물 내보내기 — PDF·Word (feature-map B17).
 *
 * 조판은 서버 시험 37개가 값으로 붙잡는다(`test_paper.py`,
 * `test_wiki_service.py`). 브라우저로 보는 것은 **사슬이 이어졌는가**다:
 *
 * - 버튼이 있고, 누르면 **파일이 떨어진다.** 액세스 토큰은 메모리에만 있어서
 *   `<a href>` 로는 못 받는다 — fetch 로 받아 blob 으로 저장하는 길이 실제로
 *   도는지는 브라우저에서만 확인된다.
 * - 받은 것이 **그 형식인지** 본다. 파일 이름만 맞고 안이 오류 JSON 인 경우가
 *   있다(그때도 저장은 된다).
 * - 문서 하나와 스페이스 한 부, 둘 다.
 */

import { readFileSync } from 'node:fs'

import type { Download, Page } from '@playwright/test'

import {
  createSpace,
  expect,
  signIn,
  test,
  uniqueKey,
  writeBody,
} from './fixtures'

function spaceKey(): string {
  return uniqueKey('X')
}

/** 내려받은 파일의 앞부분. 형식은 머리 몇 바이트로 갈린다. */
async function head(download: Download, bytes = 5): Promise<string> {
  const path = await download.path()
  return readFileSync(path).subarray(0, bytes).toString('latin1')
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

test('문서를 PDF 로 받는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Printable', '## 첫 절\n\n한글 본문이 들어간다.\n\n- [x] 끝낸 일\n')

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('article').getByRole('button', { name: /^pdf$/i }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/\.pdf$/)
  // **안을 본다.** 이름만 맞고 오류 JSON 이 담기는 경우가 있다.
  expect(await head(download)).toBe('%PDF-')

  expect(consoleErrors).toEqual([])
})

test('문서를 Word 로 받는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'Wordable', '본문 한 줄.\n')

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('article').getByRole('button', { name: /^word$/i }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/\.docx$/)
  // `.docx` 는 ZIP 이다. 머리가 `PK` 여야 워드가 연다.
  expect(await head(download, 2)).toBe('PK')

  expect(consoleErrors).toEqual([])
})

test('스페이스를 한 부의 PDF 로 받는다', async ({ page, consoleErrors }) => {
  /**
   * ZIP 내보내기와 **다른 일**이다: ZIP 은 옮기기 위한 것, 이쪽은 읽히기
   * 위한 것 — 문서 트리 순서가 장 순서인 한 부의 책이다.
   */
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await newPage(page, key, 'First', '첫 문서.\n')
  await newPage(page, key, 'Second', '둘째 문서.\n')

  await page.goto(`/wiki/${key}`)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    // 문서 쪽 버튼과 이름이 같으므로 **스페이스 쪽**을 집는다. 그 묶음에
    // 이름이 있어서 집을 수 있다 — 없으면 둘 중 하나를 찍는 수밖에 없고,
    // 그건 사람도 스크린리더로는 구별할 수 없다는 뜻이다.
    page
      .getByRole('group', { name: /export this space/i })
      .getByRole('button', { name: /^pdf$/i })
      .click(),
  ])
  expect(download.suggestedFilename()).toBe(`${key}.pdf`)
  expect(await head(download)).toBe('%PDF-')

  expect(consoleErrors).toEqual([])
})
