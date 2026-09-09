/**
 * 자산·구성 항목과 티켓의 연결 (feature-map C15).
 *
 * 서버 시험 41개가 규칙을 붙잡고 있다. 브라우저로 한 번 더 보는 이유는 이
 * 기능이 **두 화면 사이의 왕복**이기 때문이다:
 *
 * - 대장에서 종류를 만들고 자산을 등록한다. 종류가 없으면 자산을 만들 수
 *   없고, 그 사실이 화면에 보여야 한다.
 * - 티켓에서 그 자산을 **검색으로** 찾아 잇는다. 드롭다운이 아니라 검색인
 *   이유는 이 저장소가 세 번 겪은 일이다.
 * - 대장으로 돌아오면 티켓 수가 늘어 있고, 눌러 그 티켓을 읽을 수 있다 —
 *   자꾸 고장나는 장비를 찾는 것이 이 기능을 쓰는 이유다.
 * - 이어진 자산은 **지워지지 않는다.** 이력이 곧 이 기능의 값이다.
 */

import type { Locator, Page } from '@playwright/test'

import {
  API,
  createIssue,
  createProject,
  expect,
  signIn,
  stepUpToken,
  test,
  uniqueKey,
} from './fixtures'

test.slow()

/**
 * 대장에서 이 자산의 줄. **전체 목록에 단언하지 않는다** — 대장은 설치
 * 전체의 목록이고, 다른 시험이 넣은 자산이 늘 함께 보인다. "티켓 없음" 을
 * 목록 전체에서 찾으면 두 번째 자산이 생긴 날 붉어진다.
 */
function assetRow(page: Page, name: string): Locator {
  return page.getByTestId('assets').locator('li').filter({ hasText: name })
}

test.describe('자산', () => {
  test('대장에 등록하고 티켓에 잇고, 대장에서 그 티켓을 읽는다', async ({
    page,
    consoleErrors,
  }) => {
    await signIn(page)
    const projectKey = uniqueKey('S')
    await createProject(page, projectKey)
    const issueKey = await createIssue(page, projectKey, '프로젝터가 안 켜집니다')

    const typeName = `프로젝터 ${uniqueKey('T')}`
    const assetName = `본관 프로젝터 ${uniqueKey('A')}`
    const tag = uniqueKey('PJ')

    await page.goto('/settings/assets')
    await expect(page.getByRole('heading', { name: /^assets$/i, level: 1 })).toBeVisible()

    // 1. 종류부터. 종류 없이는 자산을 등록할 수 없다.
    //
    // "종류가 하나도 없다" 는 화면은 여기서 못 본다 — 종류는 설치 전체에서
    // 공유되고, 이 시험이 한 번 돌면 종류가 남는다. 빈 화면을 확인하려고
    // 시험을 첫 실행에만 통하게 만들면 두 번째 실행부터는 거짓으로 붉어진다.
    // 그 판단(`usableTypes`)은 AssetsScreen.test.ts 가 붙잡고 있고, 여기서는
    // 그 대신 **결과**를 본다: 종류를 안 고르면 등록 버튼이 안 눌린다.
    await page.getByLabel(/^new type$/i).fill(typeName)
    await page.getByRole('button', { name: /^add$/i }).click()
    await expect(page.getByText(typeName).first()).toBeVisible()

    // 2. 자산.
    await page.getByRole('button', { name: /register an asset/i }).click()
    // 폼 안에서 찾는다. 필터 줄에도 같은 이름의 칸들이 있어서, 화면 전체에서
    // 찾으면 필터를 채우고 폼은 빈 채로 남는다.
    const form = page.getByTestId('asset-form')
    await form.getByLabel(/^name$/i).fill(assetName)
    // 이름만 채워도 아직 못 만든다. 종류 없는 자산은 대장에서 쓸 데가 없다.
    await expect(form.getByRole('button', { name: /^create$/i })).toBeDisabled()
    await form.getByLabel(/^type$/i).selectOption({ label: typeName })
    await expect(form.getByRole('button', { name: /^create$/i })).toBeEnabled()
    // 소문자로 넣어도 대문자로 저장된다.
    await form.getByLabel(/^asset tag$/i).fill(tag.toLowerCase())
    await form.getByLabel(/where it is/i).fill('본관 3층')
    await form.getByRole('button', { name: /^create$/i }).click()

    const made = assetRow(page, assetName)
    await expect(made.getByText(tag)).toBeVisible()
    await expect(made.getByRole('button', { name: /no tickets/i })).toBeVisible()

    // 3. 티켓에서 **검색으로** 찾아 잇는다.
    await page.goto(`/issues/${issueKey}`)
    await page.getByRole('button', { name: /link an asset/i }).first().click()
    const find = page.getByLabel(/find an asset/i)
    await find.fill(assetName)
    await page.getByRole('button', { name: new RegExp(assetName) }).click()

    const panel = page.getByTestId('issue-assets')
    await expect(panel.getByText(assetName)).toBeVisible()
    await expect(panel.getByText(tag)).toBeVisible()
    await expect(panel.getByText(/in use/i).first()).toBeVisible()

    // 4. 대장에 돌아오면 티켓 수가 늘어 있고, 눌러 읽을 수 있다.
    await page.goto('/settings/assets')
    await page.getByLabel(/find an asset/i).fill(assetName)
    const row = assetRow(page, assetName)
    await expect(row.getByRole('button', { name: /1 ticket/i })).toBeVisible()
    await row.getByRole('button', { name: /1 ticket/i }).click()
    await expect(row.getByText('프로젝터가 안 켜집니다')).toBeVisible()

    // 5. 이어진 자산은 지워지지 않는다 — 이력이 이 기능의 값이다.
    await row.getByRole('button', { name: /^delete$/i }).click()
    await expect(page.getByText(/mark it retired instead/i)).toBeVisible()

    // 삭제 거절(409)만 뺀다 — 바로 위에서 일부러 낸 것이고, 그것이 이 기능의
    // 규칙이다. 나머지 4xx 는 하나도 없어야 한다.
    expect(consoleErrors.filter((e) => !e.startsWith('409 '))).toEqual([])
  })

  test('고객 조직은 검색으로 고르고, 2FA 를 다시 묻지 않는다', async ({
    page,
    consoleErrors,
  }) => {
    /**
     * **이 시험이 붙잡는 것은 권한이다.**
     *
     * 조직 관리 목록(`/customer-organizations`)은 step-up 을 요구한다. 자산
     * 등록은 일부러 아니다 — 장비를 하루에 스무 개 넣는 사람에게 2FA 를 스무
     * 번 물으면 그 사람은 스프레드시트로 돌아간다. 그래서 이 화면은
     * `/assets/organizations` 로 묻는다.
     *
     * 처음 만들 때는 관리 목록을 그대로 썼고, 화면을 여는 것만으로 403 이
     * 콘솔에 두 줄 찍혔다. 여기서는 **2FA 를 통과하지 않은 세션으로** 화면을
     * 몰고, 콘솔이 비어 있는 것으로 그것을 본다.
     */
    await signIn(page)

    // 조직 자체는 만드는 데 step-up 이 필요하다 — 그건 이 시험의 대상이
    // 아니므로 API 로 넘긴다. 화면은 아래에서 평범한 세션으로 몬다.
    const orgName = `한빛학교 ${uniqueKey('O')}`
    const token = await stepUpToken(page)
    const made = await page.request.post(`${API}/api/v1/customer-organizations`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: orgName, domains: [], note: null },
    })
    expect(made.ok(), await made.text()).toBe(true)

    const typeName = `노트북 ${uniqueKey('T')}`
    const assetName = `그 학교 노트북 ${uniqueKey('A')}`

    await page.goto('/settings/assets')
    await page.getByLabel(/^new type$/i).fill(typeName)
    await page.getByRole('button', { name: /^add$/i }).click()
    await expect(page.getByText(typeName).first()).toBeVisible()

    await page.getByRole('button', { name: /register an asset/i }).click()
    const form = page.getByTestId('asset-form')
    await form.getByLabel(/^type$/i).selectOption({ label: typeName })
    await form.getByLabel(/^name$/i).fill(assetName)

    // 한 글자도 안 쳤으면 후보를 안 보여 준다 — 앞의 열 개를 보여 주면
    // "이게 전부" 로 읽힌다.
    await expect(form.getByRole('button', { name: new RegExp(orgName) })).toBeHidden()
    await form.getByLabel(/customer organization/i).fill(orgName.slice(0, 3))
    await form.getByRole('button', { name: new RegExp(orgName) }).click()
    // 고른 것이 화면에 남는다. 검색칸이 그대로면 무엇을 골랐는지 알 수 없다.
    await expect(form.getByText(orgName)).toBeVisible()

    await form.getByRole('button', { name: /^create$/i }).click()

    const listed = assetRow(page, assetName)
    await expect(listed.getByText(orgName)).toBeVisible()

    expect(consoleErrors).toEqual([])
  })

  test('한 글자도 안 치면 후보를 보여 주지 않는다', async ({ page }) => {
    /**
     * 첫 스무 개를 보여 주면 "이게 전부인가" 로 읽힌다. 그리고 자산은 수천
     * 개가 되므로 처음부터 목록을 내려서는 안 된다.
     */
    await signIn(page)
    const projectKey = uniqueKey('S')
    await createProject(page, projectKey)
    const issueKey = await createIssue(page, projectKey, '무엇이 문제인지 모릅니다')

    await page.goto(`/issues/${issueKey}`)
    await page.getByRole('button', { name: /link an asset/i }).first().click()
    const panel = page.getByTestId('issue-assets')
    await expect(panel.getByLabel(/find an asset/i)).toBeVisible()
    // 후보 목록은 비어 있다. "없다" 는 말도 아직 하지 않는다 — 안 물어봤다.
    await expect(panel.getByRole('list', { name: /matches/i }).getByRole('button')).toHaveCount(0)
  })
})
