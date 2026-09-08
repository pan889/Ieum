/**
 * 디렉티브 = 매크로 (wiki-markdown.md 3절).
 *
 * 브라우저에서만 잡히는 것들이 있다: 문서를 조각으로 나눠 그리므로 제목 `id`
 * 번호가 조각을 넘어 이어져야 목차 링크가 맞고, `::issues` 는 서버 질의를
 * 실제로 돌려야 결과가 나온다.
 */

import { bodyField, createIssue, createProject, createSpace, expect, projectKey, signIn, test, uniqueKey, writeBody } from './fixtures'
import type { Page } from '@playwright/test'

function spaceKey(): string {
  return uniqueKey('D')
}

async function writePage(page: Page, title: string, body: string): Promise<void> {
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill(title)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, body)
  await page.getByRole('button', { name: /^save$/i }).click()
  // **닫힐 때까지 기다린다.** 저장을 누른 것만으로 다음 줄로 넘어가면, 아직
  // 떠 있는 편집기의 Title 칸과 곧 열 대화상자의 Title 칸이 겹쳐 선택자가
  // 둘을 잡는다 — 앱은 맞게 굴러가는데 테스트만 이따금 붉어진다.
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
}

test(':::info 는 강조 상자로 그려진다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Boxes', ':::warning\n**조심**하세요.\n\n- 하나\n- 둘\n:::')

  // 원문이 그대로 보이면 안 된다.
  await expect(page.getByText(':::warning')).toBeHidden()
  const box = page.locator('.ieum-admonition')
  await expect(box).toBeVisible()
  await expect(box).toContainText('Warning')
  await expect(box.getByRole('listitem').first()).toHaveText('하나')

  // 닫는 `:::` 이 마지막 목록 항목으로 빨려 들어가면 안 된다 (정규화 회귀).
  await page.reload()
  await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(await bodyField(page)).toHaveValue(
    ':::warning\n**조심**하세요.\n\n- 하나\n- 둘\n:::',
  )

  expect(consoleErrors).toEqual([])
})

test('::toc 는 문서 제목으로 목차를 만든다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Contents', '::toc{depth=2}\n\n## 준비\n\n본문\n\n## 배포\n\n본문\n\n### 자세히')

  const toc = page.getByRole('navigation', { name: /on this page/i })
  await expect(toc.getByRole('link', { name: '준비' })).toBeVisible()
  // depth=2 라 `###` 은 빠진다.
  await expect(toc.getByRole('link', { name: '자세히' })).toHaveCount(0)

  // 링크가 실제 제목을 가리킨다 — 조각으로 나눠 그려도 id 가 맞아야 한다.
  await expect(toc.getByRole('link', { name: '배포' })).toHaveAttribute('href', '#배포')
  await expect(page.locator('h2#배포')).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::children 은 하위 문서를 나열한다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await writePage(page, 'Parent', '::children')
  await page.getByRole('link', { name: 'Parent' }).hover()
  await page.getByRole('button', { name: /new page under parent/i }).click()
  await page.getByLabel(/^title$/i).fill('Child One')
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByRole('heading', { name: 'Child One' })).toBeVisible()

  // 트리와 빵부스러기 양쪽에 'Parent' 링크가 있다. 주소로 간다.
  await page.goto(`/wiki/${key}/parent`)
  const list = page.getByRole('navigation', { name: /child pages/i })
  await expect(list.getByRole('link', { name: 'Child One' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::children 은 이슈 설명에서는 쓸 수 없다고 말한다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'Directive out of place')

  await page.getByRole('button', { name: /^edit$/i }).first().click()
  await writeBody(page, '::children')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 조용히 비워 두면 왜 안 나오는지 알 수 없다.
  await expect(page.getByText(/only works inside a wiki page/i)).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('::issues 는 IQL 결과를 표로 그린다', async ({ page, consoleErrors }) => {
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, 'Directive target')

  const space = spaceKey()
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await writePage(
    page,
    'Dashboard',
    `::issues{query="project = ${key}" columns="key,summary,status"}`,
  )

  const table = page.getByRole('table')
  await expect(table.getByRole('cell', { name: 'Directive target' })).toBeVisible()
  await expect(table.getByRole('link', { name: `${key}-1` })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('읽을 수 없는 매크로는 저장을 막는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Broken')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '::toc{depth=99}')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 보는 시점에 처음 알면 쓴 사람은 이미 떠나고 없다.
  await expect(page.getByRole('alert')).toContainText(/depth/i)

  // 저장이 막힌 것은 의도한 422 다.
  expect(consoleErrors.filter((e) => !e.startsWith('422'))).toEqual([])
})

test('::excerpt 는 다른 문서의 앞부분을 끌어온다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await writePage(
    page,
    'Deploy policy',
    '# 배포 정책\n\n운영 배포는 화요일과 목요일에만 한다.\n\n두 번째 문단.',
  )
  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Runbook', `::excerpt{page="${key}/deploy-policy"}`)

  // 제목은 원문으로 가는 링크다.
  await expect(page.getByRole('link', { name: 'Deploy policy' }).last()).toBeVisible()
  // 앞부분만 끌어온다. 문서 전체를 박아 넣으면 인용이 아니라 복사다.
  await expect(page.getByText('운영 배포는 화요일과 목요일에만 한다.')).toBeVisible()
  await expect(page.getByText('두 번째 문단.')).toHaveCount(0)

  expect(consoleErrors).toEqual([])
})

test('::excerpt 는 없는 문서를 지어내지 않는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await writePage(page, 'Broken', `::excerpt{page="${key}/nope"}`)

  // "권한이 없다" 라고 말하지 않는다 — 그 문서가 있다는 뜻이 되어 버린다.
  await expect(page.getByText(/doesn't exist, or you can't see it/i)).toBeVisible()

  // 없는 문서를 물어본 404 는 의도한 것이다.
  expect(consoleErrors.filter((e) => !e.startsWith('404'))).toEqual([])
})

test('page 없는 ::excerpt 는 저장을 막는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)

  await page.goto(`/wiki/${key}`)
  await page.getByRole('button', { name: /^\+ new page$/i }).click()
  await page.getByLabel(/^title$/i).fill('Needs page')
  await page.getByRole('button', { name: /^create$/i }).click()
  await page.getByRole('button', { name: /^edit$/i }).click()
  await writeBody(page, '::excerpt')
  await page.getByRole('button', { name: /^save$/i }).click()

  // 보는 시점에 처음 알면 쓴 사람은 이미 떠나고 없다.
  await expect(page.getByText(/needs a page/i)).toBeVisible()

  // 저장이 막힌 것은 의도한 422 다.
  expect(consoleErrors.filter((e) => !e.startsWith('422'))).toEqual([])
})

test('::chart 는 IQL 집계를 막대로 그린다', async ({ page, consoleErrors }) => {
  /**
   * 세는 것은 리포트와 **같은 자리**다(A29). 브라우저로 보는 것은 사슬이
   * 이어졌는가와, 그림이 **글자로도 읽히는가**다 — 색과 길이만으로 뜻을
   * 전하면 화면 낭독기에는 아무것도 안 남는다(ux-principles 5절).
   */
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  await createIssue(page, key, '첫 일')
  await createIssue(page, key, '둘째 일')

  const space = spaceKey()
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await writePage(page, 'Chart', `::chart{query="project = ${key}" group=status}`)

  const chart = page.locator('.ieum-directive')
  await expect(chart).toBeVisible()
  // 이름과 수가 글자로 있어야 한다. 막대는 보조다. 시드 워크플로우의 첫
  // 상태가 `Open` 이므로 두 이슈가 그 한 칸에 모인다.
  await expect(chart.getByRole('rowheader', { name: 'Open' })).toBeVisible()
  await expect(chart.getByRole('cell', { name: '2' })).toBeVisible()
  // 숫자에서 목록으로 갈 수 있어야 한다.
  const open = chart.getByRole('link')
  await expect(open).toHaveAttribute('href', new RegExp(`iql=.*${key}`))

  expect(consoleErrors).toEqual([])
})

test('::chart 는 그리지 않은 칸이 몇 개인지 말한다', async ({ page, consoleErrors }) => {
  // 조용히 자르면 문서가 "이게 전부" 라고 거짓말을 한다.
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)
  // **칸이 둘이어야 자를 것이 생긴다.** 우선순위를 다르게 준다.
  await createIssue(page, key, '급한 일', { priority: '1' })
  await createIssue(page, key, '느긋한 일', { priority: '4' })

  const space = spaceKey()
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await writePage(page, 'Cut', `::chart{query="project = ${key}" group=priority limit=1}`)

  const chart = page.locator('.ieum-directive')
  await expect(chart).toBeVisible()
  await expect(chart.getByRole('rowheader')).toHaveCount(1)
  // 총계는 두 건이고 그린 막대는 하나다. 그 차이를 말해야 한다.
  await expect(chart).toContainText('2 issues in total')
  await expect(chart).toContainText(/1 more group not shown/i)

  expect(consoleErrors).toEqual([])
})

test('::chart 의 기준 이름이 틀리면 이유를 말한다', async ({ page, consoleErrors }) => {
  /**
   * 셀 수 있는 기준은 서버가 들고 있다. 화면이 목록을 베껴 두면 기준이 하나
   * 늘 때 화면만 모르므로, 판정은 서버에 맡기고 **그 이유를 그린다.**
   * 저장은 통과해야 한다 — 커널은 기준 목록을 모른다.
   */
  const key = projectKey()
  await signIn(page)
  await createProject(page, key)

  const space = spaceKey()
  await createSpace(page, space)
  await page.goto(`/wiki/${space}`)
  await writePage(page, 'Bad group', `::chart{query="project = ${key}" group=summary}`)

  // 저장이 됐고(제목이 보인다), 그 자리에 이유가 그려진다.
  await expect(page.getByRole('heading', { name: 'Bad group' })).toBeVisible()
  await expect(page.locator('.ieum-directive')).toContainText(/count|기준/i)

  expect(consoleErrors).toEqual([])
})
