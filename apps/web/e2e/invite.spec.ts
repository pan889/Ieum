/**
 * 초대 → 계정 활성화.
 *
 * 이 흐름은 한동안 죽어 있었다. 초대는 사용자를 만들고 사건을 남겼지만 그
 * 사건을 받는 사람이 없어서 **토큰이 아무에게도 안 갔다**. 화면에는 초대
 * 버튼이 있고 서버에는 수락 엔드포인트가 있는데, 그 사이가 비어 있었다.
 *
 * 그래서 여기서는 토큰을 몰래 만들지 않는다. 실제로 나간 메일을 읽는다 —
 * 지름길로 가면 다시 끊어져도 테스트는 통과한다.
 */

import { expect, inviteUser, signIn, test, uniqueKey } from './fixtures'

// 초대는 메일을 기다린다. 아웃박스는 15초마다 훑으므로 보통 한도로는 모자란다.
test.slow()

test('초대 메일의 주소로 계정을 연다', async ({ page, consoleErrors }) => {
  await signIn(page)

  // 메일을 읽고 비밀번호까지 정한다. 그게 이 헬퍼가 하는 일이다.
  const invited = await inviteUser(page)

  // 새 비밀번호로 실제로 들어가진다.
  await signIn(page, invited)
  await expect(page.getByText(invited.displayName)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('초대 코드가 없는 주소는 그렇게 말한다', async ({ page, consoleErrors }) => {
  // 조용히 로그인 화면으로 보내면 왜 안 되는지 아무도 모른다.
  await page.goto('/invite')
  await expect(page.getByText(/missing its invitation code/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('초대받은 사람에게는 아무 권한도 없다', async ({ page, consoleErrors }) => {
  await signIn(page)
  const invited = await inviteUser(page)
  const key = uniqueKey('I')

  await signIn(page)
  await page.goto('/wiki')
  await page.getByRole('button', { name: /new space/i }).click()
  await page.getByLabel(/^key$/i).fill(key)
  await page.getByLabel(/^name$/i).fill(`Docs ${key}`)
  await page.getByRole('button', { name: /create space/i }).click()
  await expect(page.getByRole('button', { name: /create space/i })).toHaveCount(0)

  // 계정이 생겼다는 것과 무엇을 볼 수 있다는 것은 다른 일이다.
  await signIn(page, invited)
  await page.goto(`/wiki/${key}`)
  await expect(page.getByText(/don't have permission/i)).toBeVisible()

  // 볼 수 없는 스페이스를 열었으니 403 이 나는 게 맞다. 그것 **하나만** 있어야
  // 한다 — 다른 요청까지 막히면 화면이 다른 이유로 비어 있는 것이다.
  expect(consoleErrors).toEqual([
    expect.stringMatching(new RegExp(`^403 .*/spaces/by-key/${key}$`)),
  ])
})
