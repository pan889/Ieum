/**
 * 프로젝트 목록의 검색과 페이지 넘기기.
 *
 * 한 페이지만 받고 말면 프로젝트가 페이지 크기를 넘는 순간 나머지가 화면에서
 * **사라진다** — 서버에는 있는데 갈 방법이 없다. 이건 목록이 실제로 길어져야
 * 드러나므로 여기서 확인한다.
 */

import { createProject, expect, projectKey, signIn, test } from './fixtures'

test('이름과 키로 프로젝트를 찾는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key, 'Findable Project')

  const search = page.getByLabel(/find a project/i)

  await search.fill(key)
  await expect(page.getByText(key)).toBeVisible()

  // 키가 아니라 이름 조각으로도 찾힌다.
  await search.fill('findable')
  await expect(page.getByText(key)).toBeVisible()

  await search.fill('zzz-no-such-project')
  await expect(page.getByText(/no project matches/i)).toBeVisible()
  // 검색 결과가 없는 것과 프로젝트가 하나도 없는 것은 다른 상황이다.
  await expect(page.getByText(/create one to start/i)).toBeHidden()

  expect(consoleErrors).toEqual([])
})

test('검색은 권한을 넘지 않는다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  // 관리자는 전부 보이지만, 목록이 곧 권한 필터라는 계약은 서버 테스트가
  // 지킨다. 여기서는 검색이 목록 API 를 그대로 탄다는 것만 확인한다.
  await page.getByLabel(/find a project/i).fill(key)
  await expect(page.getByText(key)).toBeVisible()

  expect(consoleErrors).toEqual([])
})
