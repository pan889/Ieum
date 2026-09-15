/**
 * 밝게 / 어둡게.
 *
 * **팔레트가 있는 것과 켤 수 있는 것은 다르다.** 어두운 토큰은 처음부터
 * `tokens.css` 에 다 적혀 있었는데, `data-theme` 을 찍는 코드가 어디에도
 * 없어서 아무도 그 화면을 볼 수 없었다. 유닛 테스트는 그런 것을 못 잡는다 —
 * 값은 다 맞으니까.
 *
 * 그래서 여기서 보는 것은 **칠해진 색**이다: 고르고 나면 본문 배경이 실제로
 * 어두워지는가, 그리고 새로고침 뒤에도 그대로인가.
 */

import { expect, signIn, test } from './fixtures'

/** `rgb(r, g, b)` 의 평균 밝기. 색 이름이 아니라 밝기로 본다. */
function lightness(color: string): number {
  const found = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(color)
  if (found === null) throw new Error(`색을 못 읽었다: ${color}`)
  return (Number(found[1]) + Number(found[2]) + Number(found[3])) / 3
}

async function bodyLightness(page: import('@playwright/test').Page): Promise<number> {
  const color = await page
    .locator('body')
    .evaluate((el) => getComputedStyle(el).backgroundColor)
  return lightness(color)
}

test('화면 색을 어둡게 골랐다가 되돌린다', async ({ page, consoleErrors }) => {
  await signIn(page)
  await page.goto('/')

  const picker = page.getByLabel(/^appearance$/i)
  await picker.selectOption('light')
  const light = await bodyLightness(page)
  expect(light).toBeGreaterThan(200)

  await picker.selectOption('dark')
  // **실제로 칠해져야 한다.** 속성만 보면, 속성은 찍히는데 CSS 가 그 갈래를
  // 모르는 상태를 통과시킨다.
  await expect.poll(() => bodyLightness(page)).toBeLessThan(80)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')

  // 고른 것은 남아야 한다. 새로고침마다 밝아지면 고른 적이 없는 것과 같다.
  await page.reload()
  await expect(page.getByLabel(/^appearance$/i)).toHaveValue('dark')
  expect(await bodyLightness(page)).toBeLessThan(80)

  // 뒤따르는 시험들이 밝은 화면을 본다.
  await page.getByLabel(/^appearance$/i).selectOption('light')
  await expect.poll(() => bodyLightness(page)).toBeGreaterThan(200)

  expect(consoleErrors).toEqual([])
})
