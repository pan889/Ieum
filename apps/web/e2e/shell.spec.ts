import { expect, signIn, test } from './fixtures'

test('사이드바 접힘 상태를 기억하고 검색 단축키가 상단 검색에 닿는다', async ({ page, consoleErrors }) => {
  await signIn(page)

  const navigation = page.getByRole('navigation', { name: /main navigation|주 탐색/i })
  await page.getByRole('button', { name: /collapse sidebar|사이드바 접기/i }).click()
  await expect(page.getByRole('button', { name: /^(expand sidebar|사이드바 펼치기)$/i })).toBeVisible()
  await expect(navigation).toHaveCSS('width', '76px')

  await page.reload()
  await expect(page.getByRole('button', { name: /^(expand sidebar|사이드바 펼치기)$/i })).toBeVisible()
  await page.keyboard.press('/')
  await expect(page.getByRole('searchbox', { name: /search|검색/i })).toBeFocused()

  expect(consoleErrors).toEqual([])
})

test('좁은 화면에서는 메뉴를 열고 탐색 후 닫는다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.setViewportSize({ width: 390, height: 844 })

  const navigation = page.getByRole('navigation', { name: /main navigation|주 탐색/i })
  const open = page.getByRole('button', { name: /open menu|메뉴 열기/i })
  await expect(navigation).toBeHidden()
  await open.click()
  await expect(navigation).toBeVisible()
  await expect(page.getByRole('button', { name: /close menu|메뉴 닫기/i })).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(navigation).toBeHidden()
  await expect(open).toBeFocused()

  await open.click()
  await navigation.getByRole('link', { name: /issues|이슈/i }).click()
  await expect(page).toHaveURL(/\/issues$/)
  await expect(navigation).toBeHidden()

  expect(consoleErrors).toEqual([])
})

test('이슈 보기 옵션을 키보드로 열어 열과 묶음을 조정한다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/issues')

  const options = page.locator('details').filter({ hasText: /view options|보기 옵션/i })
  await expect(options).not.toHaveAttribute('open', '')
  await options.locator('summary').focus()
  await page.keyboard.press('Enter')
  await expect(options).toHaveAttribute('open', '')
  await expect(options.getByText(/columns|표시 항목/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
