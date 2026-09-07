/**
 * 워크플로우 조회와 커스텀 필드 정의 화면.
 *
 * 이 화면이 생기기 전까지 필드 정의를 넣는 유일한 길은
 * `python -m ieum.cli seed-fields` 였다. E2E 도 그것에 의존했다.
 *
 * 브라우저에서만 드러나는 것: step-up 이 걸린 화면이 거절을 보여 주는가,
 * 지우기 전에 **값이 몇 개 사라지는지** 말해 주는가, 그리고 고칠 수 없는
 * 자리에 왜 그런지 적혀 있는가.
 */

import { expect, signIn, signInWithMfa, test, uniqueKey } from './fixtures'

test.describe('워크플로우', () => {
  test('2FA 없는 관리자에게는 거절을 그대로 보여 준다', async ({ page }) => {
    await signIn(page)
    await page.goto('/settings/workflows')
    await expect(page.getByRole('alert').first()).toContainText(/two-factor|2단계/i)
  })

  test('상태와 전이를 보여 준다 — 전역 전이까지', async ({ page }) => {
    await signInWithMfa(page)
    await page.goto('/settings/workflows')

    const row = page.getByRole('listitem').filter({ hasText: 'Default' })
    await expect(row).toBeVisible()
    // 어떤 이슈 유형이 쓰는지. 고칠 때의 영향 범위다.
    await expect(row).toContainText(/used by/i)

    await row.getByRole('button', { name: /show the states and transitions of Default/i }).click()
    await expect(row.getByRole('list', { name: /^states$/i })).toBeVisible()
    await expect(row.getByRole('list', { name: /^transitions$/i })).toBeVisible()
    // 시작 상태가 표시된다 — 새 이슈가 어디서 태어나는지가 그것이다.
    await expect(row).toContainText(/· start/i)

    // 편집 손잡이가 없다. 대신 왜 없는지 적혀 있다.
    await expect(row).toContainText(/can't be edited here yet/i)
  })
})

test.describe('커스텀 필드', () => {
  test('만들고, 고치고, 값과 함께 지운다', async ({ page }) => {
    const key = `cf_${uniqueKey('e').toLowerCase()}`

    await signInWithMfa(page)
    await page.goto('/settings/fields')

    await page.getByRole('button', { name: /^new field$/i }).click()
    await page.getByLabel(/^key$/i).fill(key)
    await page.getByLabel(/^label$/i).fill('Severity')
    await page.getByLabel(/^type$/i).selectOption('select')
    // 선택지 칸은 종류를 고른 **뒤에** 나타난다.
    await page.getByLabel(/^choices$/i).fill('low, high')
    await page.getByRole('button', { name: /^create field$/i }).click()
    await expect(page.getByRole('button', { name: /^create field$/i })).toHaveCount(0)

    const row = page.getByRole('listitem').filter({ hasText: key })
    await expect(row).toBeVisible()
    await expect(row).toContainText('Severity')
    await expect(row).toContainText(/single choice/i)
    await expect(row).toContainText(/everywhere/i)
    // 아직 아무 이슈도 값을 안 넣었다.
    await expect(row).toContainText(/no values yet/i)

    // 이름을 고친다. 키와 종류는 폼에 없고, 왜 없는지 적혀 있다.
    await row.getByRole('button', { name: /^edit Severity$/i }).click()
    await expect(row).toContainText(/key and the type can't be changed/i)
    await expect(row.getByLabel(/^key$/i)).toHaveCount(0)
    await row.getByLabel(/^label$/i).fill('Impact')
    await row.getByRole('button', { name: /^save$/i }).click()
    await expect(row).toContainText('Impact')

    // 필수로 걸고 되돌린다. 거는 길만 있으면 잘못 건 것을 풀 수 없다.
    // 배지 글자로 보지 않는다 — 버튼 이름에도 "required" 가 들어 있어서
    // 그 단언은 토글 전에도 참이 된다(그러면 아무것도 확인하지 않는다).
    const toggle = row.getByRole('button', { name: /change whether Impact is required/i })
    await expect(toggle).toHaveText(/^make required$/i)
    await toggle.click()
    await expect(toggle).toHaveText(/^make optional$/i)
    await toggle.click()
    await expect(toggle).toHaveText(/^make required$/i)

    // 지우기는 두 번 눌러야 하고, 값이 몇 개 사라지는지 말해 준다.
    await row.getByRole('button', { name: /^delete the field Impact$/i }).click()
    await expect(row).toContainText(/no issue has a value for this field/i)
    await row.getByRole('button', { name: /^confirm deleting the field Impact$/i }).click()
    await expect(page.getByRole('listitem').filter({ hasText: key })).toHaveCount(0)
  })

  test('선택지 없는 단일 선택은 거절한다', async ({ page }) => {
    // 채울 수 없는 필드다. 저장을 받아 주면 화면은 빈 드롭다운을 그린다.
    const key = `cf_${uniqueKey('e').toLowerCase()}`

    await signInWithMfa(page)
    await page.goto('/settings/fields')
    await page.getByRole('button', { name: /^new field$/i }).click()
    await page.getByLabel(/^key$/i).fill(key)
    await page.getByLabel(/^label$/i).fill('Pick')
    await page.getByLabel(/^type$/i).selectOption('select')
    // 필수 표시가 붙어 있으니 브라우저가 먼저 막는다 — 그걸 비켜 서버까지
    // 보낸다. 화면 검증만 믿으면 API 를 직접 치는 쪽이 통과한다.
    await page.getByLabel(/^choices$/i).fill(' ')
    await page.getByRole('button', { name: /^create field$/i }).click()

    await expect(page.getByRole('alert').first()).toContainText(/at least one choice/i)
  })
})
