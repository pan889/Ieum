/**
 * 키보드 조작 계층 — 전역 단축키와 명령 팔레트.
 *
 * 단위 시험(`shared/keys/keys.test.ts`)이 판정을 붙잡는다. 여기서 보는 것은
 * 그 판정이 **실제 브라우저의 초점과 이어져 있는가**다. 둘은 다르다:
 * 판정이 맞아도 리스너를 안 달았거나, 초점이 엉뚱한 데 있으면 아무 일도 안
 * 난다.
 *
 * 제일 중요한 시험은 마지막 것 — **글 쓰는 중에 단축키가 튀지 않는가.**
 * 그게 튀면 사람은 쓰던 것을 잃고, 왜 그랬는지도 모른다.
 */

import { createIssue, createProject, expect, signIn, test, uniqueKey } from './fixtures'

test.describe('전역 단축키', () => {
  test('? 를 누르면 도움말이 뜨고, 거기 적힌 것이 실제로 도는 것이다', async ({ page }) => {
    await signIn(page)

    await page.keyboard.press('?')
    const help = page.getByRole('dialog', { name: /keyboard shortcuts|키보드 단축키/i })
    await expect(help).toBeVisible()

    // 목록은 `GLOBAL_SHORTCUTS` 에서 나온다. 네 개가 다 보여야 한다 —
    // 도움말에만 있고 안 도는 키가 없다는 것은 타입이 보장하고, 여기서는
    // 그 목록이 실제로 그려지는지를 본다.
    await expect(help.getByText(/command palette|명령 팔레트/i)).toBeVisible()
    await expect(help.getByText(/new issue|새 이슈/i)).toBeVisible()
    await expect(help.getByText(/focus search|검색으로 이동/i)).toBeVisible()

    await page.getByRole('button', { name: /close|닫기/i }).click()
    await expect(help).toBeHidden()
  })

  test('Cmd/Ctrl+K 로 팔레트를 열고 키보드만으로 옮겨 간다', async ({ page }) => {
    await signIn(page)

    await page.keyboard.press('ControlOrMeta+k')
    const palette = page.getByRole('dialog', { name: /command palette|명령 팔레트/i })
    await expect(palette).toBeVisible()

    // 이름 일부만 쳐서 좁힌다.
    await page.keyboard.type('wiki')
    const options = palette.getByRole('option')
    await expect(options.first()).toBeVisible()

    await page.keyboard.press('Enter')
    await expect(palette).toBeHidden()
    await expect(page).toHaveURL(/\/wiki/)
  })

  test('팔레트는 Esc 로 닫힌다', async ({ page }) => {
    await signIn(page)
    await page.keyboard.press('ControlOrMeta+k')
    const palette = page.getByRole('dialog', { name: /command palette|명령 팔레트/i })
    await expect(palette).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(palette).toBeHidden()
  })

  test('친 말을 그대로 찾는 길이 팔레트에 늘 있다', async ({ page }) => {
    await signIn(page)
    await page.keyboard.press('ControlOrMeta+k')
    // 어떤 화면 이름과도 안 맞는 말을 친다.
    await page.keyboard.type('zzqq')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/search\?.*q=zzqq/)
  })

  test('c 로 새 이슈, / 로 검색 칸', async ({ page }) => {
    await signIn(page)

    await page.keyboard.press('c')
    await expect(page).toHaveURL(/\/issues\/new/)

    await page.goto('/')
    // **셸이 뜰 때까지 기다린다.** 리스너는 마운트 뒤 effect 에서 붙으므로,
    // `goto` 가 돌아온 직후에 누르면 아무 일도 안 난다. 실제로 그렇게 붉었고,
    // 브라우저로 확인해 보니 초점이 `BODY` 에 그대로 있었다.
    const search = page.getByRole('searchbox')
    await expect(search).toBeVisible()

    await page.keyboard.press('/')
    // 초점이 검색 칸으로 갔는가. 눌러 보는 것으로 확인한다.
    await page.keyboard.type('hello')
    await expect(search).toHaveValue('hello')
  })

  test('**글 쓰는 중에는 단축키가 튀지 않는다**', async ({ page }) => {
    await signIn(page)

    // 검색 칸에 초점을 두고 단축키 글자들을 친다.
    const search = page.getByRole('searchbox')
    await search.click()
    await search.fill('')
    await page.keyboard.type('c/?')

    // 아무 데도 안 갔고, 친 글자가 그대로 들어 있다.
    await expect(page).toHaveURL(/\/$|\/#/)
    await expect(search).toHaveValue('c/?')
    await expect(page.getByRole('dialog')).toHaveCount(0)

    // 그런데 팔레트는 열려야 한다 — 검색어를 치다가도 부를 수 있어야 하니까.
    await page.keyboard.press('ControlOrMeta+k')
    await expect(page.getByRole('dialog', { name: /command palette|명령 팔레트/i })).toBeVisible()
  })
})

test.describe('목록에서', () => {
  test.slow()

  test('j·k 로 줄을 옮기고 o 로 연다', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KB')
    await createProject(page, key)
    await createIssue(page, key, '첫째 줄')
    await createIssue(page, key, '둘째 줄')

    // 이 프로젝트만 보이게 좁힌다 — 시드에 남의 이슈가 잔뜩 있다.
    await page.goto(`/issues?iql=${encodeURIComponent(`project = ${key} ORDER BY created ASC`)}`)
    // 이슈 줄에만 `aria-selected` 가 있다(묶음 머리글에는 없다). 줄 전체
    // 글자로 거르면 안 된다 — 한 줄에 키·상태·날짜가 같이 들어 있어서
    // 제목으로 끝나지 않는다. 처음에 그렇게 써서 0개를 받았다.
    const rows = page.locator('tbody tr[aria-selected]')
    await expect(rows).toHaveCount(2)
    await expect(rows.first()).toContainText('첫째 줄')
    await expect(rows.nth(1)).toContainText('둘째 줄')

    // 아직 아무 데도 안 짚었다.
    await expect(page.locator('tr[aria-selected="true"]')).toHaveCount(0)

    await page.keyboard.press('j')
    await expect(rows.first()).toHaveAttribute('aria-selected', 'true')

    await page.keyboard.press('j')
    await expect(rows.nth(1)).toHaveAttribute('aria-selected', 'true')

    // 끝에서 더 눌러도 넘어가지 않는다.
    await page.keyboard.press('j')
    await expect(rows.nth(1)).toHaveAttribute('aria-selected', 'true')

    await page.keyboard.press('k')
    await expect(rows.first()).toHaveAttribute('aria-selected', 'true')

    await page.keyboard.press('o')
    await expect(page).toHaveURL(new RegExp(`/issues/${key}-`))
    await expect(page.getByRole('heading', { name: '첫째 줄' })).toBeVisible()
  })

  test('Enter 도 같은 일을 하고, 도움말이 목록 키를 그때만 보여 준다', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KB')
    await createProject(page, key)
    await createIssue(page, key, '엔터로 연다')

    // 목록 밖(첫 화면)에서는 목록 키가 도움말에 없다.
    await page.keyboard.press('?')
    let help = page.getByRole('dialog', { name: /keyboard shortcuts|키보드 단축키/i })
    await expect(help).toBeVisible()
    await expect(help.getByText(/next row|다음 줄/i)).toHaveCount(0)
    await page.keyboard.press('Escape').catch(() => undefined)
    await page.getByRole('button', { name: /close|닫기/i }).click()

    await page.goto(`/issues?iql=${encodeURIComponent(`project = ${key}`)}`)
    await expect(page.getByRole('row').filter({ hasText: '엔터로 연다' })).toHaveCount(1)

    // 목록에 오면 도움말에 목록 묶음이 생긴다.
    await page.keyboard.press('?')
    help = page.getByRole('dialog', { name: /keyboard shortcuts|키보드 단축키/i })
    await expect(help.getByText(/next row|다음 줄/i)).toBeVisible()
    await page.getByRole('button', { name: /close|닫기/i }).click()

    await page.keyboard.press('j')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(new RegExp(`/issues/${key}-`))
  })
})

test.describe('이슈에서', () => {
  test.slow()

  /**
   * 상세 화면이 **다 그려질 때까지** 기다린다.
   *
   * `createIssue` 는 URL 이 바뀌면 돌아온다. 그 시점엔 아직 데이터가 오는
   * 중이고 단축키를 등록하는 컴포넌트가 안 붙어 있다 — 곧바로 누르면 아무
   * 일도 안 난다. 목록의 `/` 에서 이미 같은 것에 걸렸다.
   */
  async function openIssue(page: import('@playwright/test').Page, key: string, summary: string) {
    await createIssue(page, key, summary)
    await expect(page.getByRole('button', { name: /^(edit|편집)$/i })).toBeVisible()
  }

  test('e·a·p·s 가 각 자리로 데려간다', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KD')
    await createProject(page, key)
    await openIssue(page, key, '단축키로 고친다')

    // e — 편집을 연다.
    await page.keyboard.press('e')
    await expect(page.getByRole('button', { name: /^(save|저장)$/i })).toBeVisible()
    await page.getByRole('button', { name: /^(cancel|취소)$/i }).click()
    await expect(page.getByRole('button', { name: /^(edit|편집)$/i })).toBeVisible()

    // a — 담당자 고르는 칸이 뜬다.
    await page.keyboard.press('a')
    await expect(page.getByLabel(/^(assignee|담당자)$/i)).toBeVisible()

    // p — 우선순위 상자로 초점이 간다.
    const priority = page.getByRole('combobox', { name: /^(priority|우선순위)$/i })
    await page.keyboard.press('p')
    await expect(priority).toBeFocused()

    // **상자 안에서는 `s` 가 안 먹는다.** 선택 상자는 글자로 항목을 고르므로
    // 억제 대상이다 — 여기서 단축키가 튀면 그 표준 동작이 죽는다.
    // (처음엔 이걸 모르고 `p` 다음에 곧바로 `s` 를 눌러 시험이 붉었다.
    //  제품이 맞았고 시험이 틀렸다.)
    await page.keyboard.press('s')
    await expect(priority).toBeFocused()

    // 상자에서 나오면 s 가 전이 단추로 데려간다.
    await page.getByRole('heading', { name: '단축키로 고친다' }).click()
    await page.keyboard.press('s')
    await expect(priority).not.toBeFocused()
    await expect(page.locator('button:focus')).toHaveCount(1)
  })

  test('고치다 취소한 뒤 e 를 눌러도 **버린 초안이 되살아나지 않는다**', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KD')
    await createProject(page, key)
    await openIssue(page, key, '원래 제목')

    // 단추로 열어 고치다 취소한다.
    await page.getByRole('button', { name: /^(edit|편집)$/i }).click()
    const summary = page.getByLabel(/^(summary|제목)$/i)
    await summary.fill('버릴 제목')
    await page.getByRole('button', { name: /^(cancel|취소)$/i }).click()
    await expect(page.getByRole('button', { name: /^(edit|편집)$/i })).toBeVisible()

    // 이번엔 키로 연다. 단추와 같은 길을 써야 원본이 보인다 —
    // 처음엔 `e` 가 `setEditing(true)` 만 해서 버린 초안이 되살아났다.
    await page.keyboard.press('e')
    await expect(page.getByLabel(/^(summary|제목)$/i)).toHaveValue('원래 제목')
  })

  test('도움말이 이슈 화면에서만 상세 키를 보여 준다', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KD')
    await createProject(page, key)
    await openIssue(page, key, '도움말 확인')

    await page.keyboard.press('?')
    const help = page.getByRole('dialog', { name: /keyboard shortcuts|키보드 단축키/i })
    // 네 개가 **한 묶음**으로 합쳐져 보인다. 각자 다른 컴포넌트가 등록한 것이다.
    await expect(help.getByText(/on an issue|이슈에서/i)).toBeVisible()
    await expect(help.getByText(/change assignee|담당자 바꾸기/i)).toBeVisible()
    await expect(help.getByText(/go to priority|우선순위로/i)).toBeVisible()
    await page.getByRole('button', { name: /close|닫기/i }).click()

    // 첫 화면으로 가면 사라진다.
    await page.goto('/')
    await expect(page.getByRole('searchbox')).toBeVisible()
    await page.keyboard.press('?')
    await expect(
      page.getByRole('dialog').getByText(/change assignee|담당자 바꾸기/i),
    ).toHaveCount(0)
  })

  test('**코멘트를 쓰는 중에는 상세 단축키가 튀지 않는다**', async ({ page }) => {
    await signIn(page)
    const key = uniqueKey('KD')
    await createProject(page, key)
    await openIssue(page, key, '코멘트 중 안 튄다')

    // 코멘트 칸은 서식 편집기라 `contenteditable` 이다 — `input` 이 아니라서
    // 억제에서 제일 놓치기 쉬운 갈래이고, 그래서 여기를 고른다.
    const box = page.locator('[contenteditable="true"]').first()
    await box.click()
    // `please paste` 에 `e`·`a`·`s`·`p` 가 다 들어 있다.
    await page.keyboard.type('please paste')

    // 편집이 열리지 않았고, 담당자 칸도 안 떴고, 친 글자가 그대로 있다.
    await expect(page.getByRole('button', { name: /^(save|저장)$/i })).toHaveCount(0)
    await expect(page.getByLabel(/^(assignee|담당자)$/i)).toHaveCount(0)
    await expect(box).toContainText('please paste')
  })
})
