/**
 * 요청 중 문서 추천 (feature-map C8).
 *
 * 서버 시험 12개가 규칙을 붙잡고 있다. 브라우저로 한 번 더 보는 이유:
 *
 * - **이건 고객 표면이다.** 게스트도 오고, 여기 잘못 뜨는 것은 곧 유출이다 —
 *   서버 시험은 함수의 반환값을 보고, 화면이 그것을 어떻게 쓰는지는 못 본다.
 * - **팀 스페이스는 목록에 없어야 한다.** 관리자가 고를 수 있으면 실수 하나로
 *   내부 문서가 고객 화면에 뜬다.
 * - 아직 안 적었을 때 아무 것도 안 뜨는 것도 눈으로 봐야 안다.
 */

import type { Page } from '@playwright/test'

import { createPortal, createSpace, expect, signIn, test, uniqueKey } from './fixtures'

test.slow()

/** 지식베이스 스페이스 하나와 그 안의 공개 문서 한 편. */
async function kbWithArticle(page: Page, title: string): Promise<string> {
  const key = uniqueKey('KB')
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  // **지식베이스로 만든다.** 팀 스페이스는 요청 유형에 걸 수 없다.
  // 화면의 라벨은 "Type" 이다("Kind" 가 아니다) — 카탈로그의 키 이름이
  // `spaces.kind` 라서 그렇게 적었다가 시험이 라벨을 못 찾고 멈췄다.
  await page.getByLabel(/^type$/i).selectOption('kb')
  await page.getByRole('button', { name: /create space/i }).click()
  await expect(page.getByRole('button', { name: /create space/i })).toHaveCount(0)

  // 목록의 링크는 이름이 **"Open"** 이다(키는 `<code>` 안에 있다). 주소로
  // 바로 간다 — 목록에서 찾아 누르는 것은 스페이스 목록 스펙이 이미 본다.
  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  // 만들면 곧바로 발행된다(`publish: true`). 초안은 색인에 없으므로 그것이
  // 중요하다.
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  return key
}

test.describe('요청 중 문서 추천', () => {
  test('제목을 적으면 지식베이스 문서가 뜨고, 그래도 요청은 낼 수 있다', async ({ page }) => {
    await signIn(page)
    const title = `프린터 문제 해결 ${uniqueKey('A')}`
    const spaceKey = await kbWithArticle(page, title)
    const portal = await createPortal(page, { isPublic: true })

    // 요청 유형에 지식베이스를 건다.
    await page.goto('/settings/portals')
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()
    // 요청 유형 줄에서 바로 고른다. 만들 때만 걸 수 있게 두면, 폼을 먼저
    // 만들고 나중에 문서를 쓴 관리자는 이 기능에 영영 닿지 못한다.
    await page.getByRole('button', { name: /request forms/i }).first().click()
    const picker = page.getByLabel(/knowledge base/i).first()
    await expect(picker).toBeVisible()
    // `selectOption` 의 label 은 **정확한 문자열**이다(정규식이 아니다).
    // 목록은 `KEY · 이름` 으로 그리므로 그대로 만든다.
    await picker.selectOption({ label: `${spaceKey} · Docs ${spaceKey}` })

    // 고객이 제목을 적는다.
    await page.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await page.getByLabel(new RegExp(portal.formLabel)).fill('프린터')
    await expect(page.getByText(title)).toBeVisible()

    // **폼을 막지 않는다.** 추천이 틀렸을 때 사람을 가둬 두는 화면이 된다.
    await expect(page.getByRole('button', { name: /send request/i })).toBeEnabled()
  })

  test('아직 안 적었으면 아무 것도 뜨지 않는다', async ({ page }) => {
    // 목록이 먼저 뜨면 그건 추천이 아니라 스페이스 공개다.
    await signIn(page)
    const portal = await createPortal(page, { isPublic: true })
    await page.goto(`/portal/${portal.slug}/new/${portal.requestTypeId}`)
    await expect(page.getByText(/these pages might help/i)).toHaveCount(0)
  })
})

test.describe('지식베이스를 고르는 자리', () => {
  test('팀 스페이스는 목록에 없다', async ({ page }) => {
    // **관리자가 고를 수 있으면 실수 하나로 내부 문서가 고객 화면에 뜬다.**
    await signIn(page)
    const teamKey = uniqueKey('TM')
    await createSpace(page, teamKey) // 종류를 안 고르면 team 이다
    const portal = await createPortal(page)

    await page.goto('/settings/portals')
    await page.getByRole('button', { name: /change|choose|바꾸기|고르세요/i }).click()
    await page.getByRole('textbox', { name: /^project$/i }).fill(portal.projectKey)
    await page.getByRole('button', { name: new RegExp(portal.projectKey) }).click()
    await page.getByRole('button', { name: /request forms/i }).first().click()

    const picker = page.getByLabel(/knowledge base/i).first()
    await expect(picker).toBeVisible()
    await expect(picker.getByRole('option', { name: new RegExp(teamKey) })).toHaveCount(0)
  })
})
