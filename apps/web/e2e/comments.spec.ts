/**
 * 문서 코멘트와 인라인 앵커 (wiki-markdown.md 6절).
 *
 * 여기서만 잡히는 것들이 있다: 선택 범위는 렌더된 DOM 위에서만 만들어지고,
 * 하이라이트는 태그 경계를 걸치는 순간 조용히 사라진다(`surroundContents` 가
 * 던진다). 그리고 본문을 고쳤을 때 목록이 다시 오지 않으면 화면은 계속
 * "잘 붙어 있음" 이라고 말한다.
 */

import type { Page } from '@playwright/test'

import { expect, signIn, test } from './fixtures'

function spaceKey(): string {
  return 'C' + Math.random().toString(36).slice(2, 6).toUpperCase()
}

async function createSpace(page: Page, key: string): Promise<void> {
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  await page.getByLabel(/find a space/i).fill(key)
  await expect(page.getByText(key).first()).toBeVisible()
}

async function writePage(page: Page, title: string, body: string): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await edit(page, body)
}

async function edit(page: Page, body: string): Promise<void> {
  await page.getByRole('button', { name: /^edit$/i }).click()
  await page.getByLabel(/^body$/i).fill(body)
  await page.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
}

/** 본문에서 `from` 이 시작되는 곳부터 `to` 앞까지 드래그한 것으로 친다. */
async function selectInBody(page: Page, from: string, to: string): Promise<void> {
  // 저장 직후에는 본문이 아직 이전 판일 수 있다. 글자가 보일 때까지 기다린다.
  await expect(page.locator('.ieum-markdown').filter({ hasText: from })).toBeVisible()
  await page.evaluate(
    ([start, end]) => {
      const body = document.querySelector('.ieum-markdown')
      if (!body) throw new Error('본문을 못 찾았다')
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT)
      const nodes: Text[] = []
      let node = walker.nextNode()
      while (node) {
        nodes.push(node as Text)
        node = walker.nextNode()
      }
      const head = nodes.find((n) => (n.nodeValue ?? '').includes(start as string))
      const tail = nodes.find((n) => (n.nodeValue ?? '').includes(end as string))
      if (!head || !tail) throw new Error('선택할 텍스트를 못 찾았다')
      const range = document.createRange()
      range.setStart(head, (head.nodeValue ?? '').indexOf(start as string))
      range.setEnd(tail, (tail.nodeValue ?? '').indexOf(end as string))
      const selection = window.getSelection()
      selection?.removeAllRanges()
      selection?.addRange(range)
    },
    [from, to],
  )
}

async function comment(page: Page, text: string): Promise<void> {
  await page.getByRole('button', { name: /comment on selection/i }).click()
  await page.getByLabel(/^comment$/i).fill(text)
  await page.getByRole('button', { name: /^post$/i }).click()
}

test('선택한 곳에 코멘트를 달면 그 자리가 표시된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '배포 전 **마이그레이션을 검토한다**. 뒤 문장이다.')

  await selectInBody(page, '마이그레이션', '. 뒤')
  await comment(page, '이 부분 확인 부탁합니다.')

  await expect(page.getByText('이 부분 확인 부탁합니다.')).toBeVisible()
  // 인용문이 코멘트에 남는다.
  await expect(page.getByText('마이그레이션을 검토한다').first()).toBeVisible()
  // 본문에도 표시가 얹힌다.
  await expect(page.locator('mark.ieum-quote')).toHaveCount(1)

  expect(consoleErrors).toEqual([])
})

test('조금 고쳐도 인용은 다시 붙는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '배포 전 **마이그레이션을 검토한다**. 뒤 문장이다.')
  await selectInBody(page, '마이그레이션', '. 뒤')
  await comment(page, '확인 부탁')
  await expect(page.locator('mark.ieum-quote')).toHaveCount(1)

  await edit(page, '배포 전에 **마이그레이션을 꼭 검토한다**. 다른 뒤 문장.')

  await expect(page.getByText(/quote gone/i)).toHaveCount(0)
  // 표시는 서버가 실제로 찾은 글자를 따라간다. 태그 경계를 걸치므로
  // 조각으로 나뉜다 — 하나로 감싸려 하면 던지고 표시가 통째로 사라진다.
  await expect(page.locator('mark.ieum-quote').first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('인용이 사라지면 고아로 남는다 — 조용히 지우지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '배포 전 마이그레이션을 검토한다. 뒤 문장이다.')
  await selectInBody(page, '마이그레이션', '. 뒤')
  await comment(page, '확인 부탁')

  await edit(page, '완전히 다른 내용만 남았다.')

  await expect(page.getByText(/quote gone/i)).toBeVisible()
  // 코멘트도, 무엇에 달았는지도 남는다.
  await expect(page.getByText('확인 부탁')).toBeVisible()
  await expect(page.getByText('마이그레이션을 검토한다')).toBeVisible()
  await expect(page.locator('mark.ieum-quote')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('되돌리면 고아가 다시 붙는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '배포 전 마이그레이션을 검토한다. 뒤 문장이다.')
  await selectInBody(page, '마이그레이션', '. 뒤')
  await comment(page, '확인 부탁')
  await edit(page, '완전히 다른 내용만 남았다.')
  await expect(page.getByText(/quote gone/i)).toBeVisible()

  // 한 번 고아면 영영 고아인 것은 틀렸다.
  await page.getByRole('button', { name: /^history$/i }).click()
  // 이력은 최신순이다. 지금 판 바로 아래가 인용이 살아 있던 판이다.
  await page.getByRole('button', { name: /^restore$/i }).first().click()

  await expect(page.getByText(/quote gone/i)).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('문서 전체에 다는 코멘트와 답글', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '본문이다.')

  // 아무것도 선택하지 않으면 문서 전체 코멘트다.
  await page.getByRole('button', { name: /comment on selection/i }).click()
  await expect(page.getByText(/on the whole page/i)).toBeVisible()
  await page.getByLabel(/^comment$/i).fill('전체적으로 좋다.')
  await page.getByRole('button', { name: /^post$/i }).click()
  await expect(page.getByText('전체적으로 좋다.')).toBeVisible()

  await page.getByRole('button', { name: /^reply$/i }).click()
  await page.getByLabel(/^comment$/i).fill('동의한다.')
  await page.getByRole('button', { name: /^post$/i }).click()
  await expect(page.getByText('동의한다.')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('해결하면 목록에서 접히고 다시 열 수 있다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', '본문이다.')

  await page.getByRole('button', { name: /comment on selection/i }).click()
  await page.getByLabel(/^comment$/i).fill('고쳐 주세요.')
  await page.getByRole('button', { name: /^post$/i }).click()
  await expect(page.getByText('고쳐 주세요.')).toBeVisible()

  await page.getByRole('button', { name: /^resolve$/i }).click()
  await expect(page.getByText('고쳐 주세요.')).toBeHidden()

  // 지우지 않는다 — 왜 그렇게 됐는지가 이력이다.
  await page.getByRole('button', { name: /show resolved/i }).click()
  await expect(page.getByText('고쳐 주세요.')).toBeVisible()
  await page.getByRole('button', { name: /^reopen$/i }).click()
  await expect(page.getByRole('button', { name: /^resolve$/i })).toBeVisible()

  expect(consoleErrors).toEqual([])
})
