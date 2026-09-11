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

import { expect, signIn, test } from './fixtures'

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
