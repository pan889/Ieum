/**
 * 다른 도구에서 옮겨 오기 (M6 "임포터").
 *
 * 서버 시험이 규칙을 붙잡고 있다. 브라우저로 한 번 더 보는 이유는 **이
 * 기능의 값이 화면에서만 드러나기 때문**이다.
 *
 * - **못 이은 낱말이 적재를 막는다.** 그것을 화면이 말하지 않으면 사람은
 *   그냥 누르고, 이관은 "성공" 하고, 몇 달 뒤에 이슈 수천 개가 아무도 안
 *   고른 상태에 있는 것을 본다. 단추가 실제로 잠기는지를 본다.
 * - **짝을 지어 주면 단추가 열린다.** 짝 → 미리 보기 다시 → 적재까지가 한
 *   왕복이다. 서버 시험은 그 왕복을 볼 수 없다.
 * - **두 번 눌러도 안 늘어난다.** 이관은 한 번에 안 끝난다 — 멈추고 며칠 뒤
 *   다시 돌리는 것이 정상이다. 사람이 실제로 두 번 누르는 것을 본다.
 * - **못 옮긴 것을 화면이 접어 두지 않는다.** 그 목록은 여기서 안 보면
 *   아무 데도 안 남는다.
 *
 * 묶음은 여기서 짓는다. 어댑터를 돌리지 않는 이유는 그쪽이 살아 있는 Redmine
 * 을 요구하기 때문이고, 이 스펙이 보려는 것은 **받는 쪽**이다.
 */

import type { Locator, Page } from '@playwright/test'

import { createProject, expect, signInWithMfa, test, uniqueKey, zip } from './fixtures'

test.slow()

/**
 * 이관 묶음 하나. 안에 든 것이 곧 이 스펙이 확인할 것들이다.
 *
 * - `Bug` 는 시드 어휘에 있다 → 이름으로 이어진다
 * - `New` 는 없다 → 못 이어서 **적재를 막는다**
 * - `ghost@example.com` 은 이 설치본에 없다 → 사람을 못 잇고, 그 이슈는
 *   작성자를 잃는다(그 사실이 "못 옮긴 것" 에 이름으로 남아야 한다)
 * - `R-2` 는 `R-1` 의 자식이고 **뒤에 온다** — 부모를 나중에 잇지 않으면
 *   못 잇는 자리다
 * - `R-3` 은 묶음 밖(`R-99`)을 가리킨다 → 그 관계는 안 생긴다
 */
function archive(): Buffer {
  const manifest = {
    format: 1,
    source: { kind: 'redmine', base_url: 'https://redmine.example.com', version: '5.1.2' },
    // `description` 은 **아예 안 쓴다.** 포맷의 쓰는 쪽이 빈 값을 지우므로
    // 읽는 쪽은 있으면 문자열이라야 한다 — `null` 을 넣으면 거절한다
    // (그렇게 넣어 보고 `imports.unreadable_archive` 를 받았다).
    project: { key: 'OPS', name: '운영' },
    taken_at: '2026-03-01T00:00:00Z',
    adapter: 'ieum.migrate 1.0.0',
    counts: { people: 2, issues: 3, comments: 2 },
  }
  const people = [
    { source_id: '1', name: '김운영', email: 'admin@example.com', active: true },
    { source_id: '2', name: '떠난 사람', email: 'ghost@example.com', active: false },
  ]
  const issues = [
    {
      source_id: 'R-1',
      summary: '디스크가 찼다',
      description: '루트 파티션 95%.',
      type: 'Bug',
      status: 'Open',
      priority: 'Normal',
      author: '1',
      created_at: '2023-04-05T09:00:00Z',
      updated_at: '2023-04-06T09:00:00Z',
      labels: ['infra'],
      comments: [
        { source_id: 'C-1', author: '1', body: '로그를 지웠다.', created_at: '2023-04-05T10:00:00Z' },
      ],
      relations: [],
      done_ratio: 0,
    },
    {
      source_id: 'R-2',
      summary: '로그 로테이션을 켠다',
      type: 'Bug',
      // **못 이을 낱말.** 이것이 적재를 막는다.
      status: 'New',
      priority: 'Normal',
      // 이 설치본에 없는 사람 → 작성자를 잃는다
      author: '2',
      created_at: '2023-04-05T11:00:00Z',
      // 부모가 **앞에** 있다. 자식이 뒤에 오는 흔한 모양.
      parent: 'R-1',
      comments: [
        { source_id: 'C-2', author: '1', body: '주간으로.', created_at: '2023-04-05T12:00:00Z' },
      ],
      relations: [],
      done_ratio: 0,
    },
    {
      source_id: 'R-3',
      summary: '모니터링을 붙인다',
      type: 'Bug',
      status: 'Open',
      priority: 'Normal',
      author: '1',
      created_at: '2023-04-07T09:00:00Z',
      relations: [
        { kind: 'relates', target: 'R-1' },
        // 묶음 밖. 이 관계는 안 생기고, 그렇다고 말해야 한다.
        { kind: 'relates', target: 'R-99' },
      ],
      comments: [],
      done_ratio: 0,
    },
  ]
  const lines = (rows: unknown[]) => rows.map((row) => JSON.stringify(row)).join('\n') + '\n'
  return zip([
    ['manifest.json', JSON.stringify(manifest, null, 2)],
    ['people.jsonl', lines(people)],
    ['issues.jsonl', lines(issues)],
  ])
}

/**
 * 상자에서 이름으로 고른다.
 *
 * `selectOption({ label })` 을 안 쓰는 이유: 상태 후보의 글자는 **워크플로우
 * 이름이 꼬리로 붙는다**(`Open (기본 워크플로우)`). 같은 이름의 상태가
 * 워크플로우마다 따로 있어서 그렇게 그린다. 이름을 통째로 적으면 시드의
 * 워크플로우 이름을 시험이 외우게 되고, 그 이름이 바뀌면 여기가 붉어진다.
 */
async function pickOption(select: Locator, name: RegExp): Promise<void> {
  const value = await select.locator('option').filter({ hasText: name }).first().getAttribute('value')
  expect(value, `고를 것을 못 찾았다: ${name}`).toBeTruthy()
  await select.selectOption(value)
}

async function upload(page: Page, projectKey: string): Promise<void> {
  await page.goto('/settings/imports')
  await expect(page.getByRole('heading', { name: /^import$/i, level: 1 })).toBeVisible()

  // 프로젝트를 찾아 고른다. 피커는 **접힌 채로** 시작하므로 먼저 펼친다 —
  // 목록을 통째로 담지 않는 피커라 검색으로 간다.
  await page.getByRole('button', { name: /^choose/i }).click()
  await page.getByLabel(/into which project/i).fill(projectKey)
  await page
    .getByRole('list', { name: /matches/i })
    .getByRole('button', { name: new RegExp(`^${projectKey} · `) })
    .click()

  await page.getByTestId('import-file').setInputFiles({
    name: 'ops.zip',
    mimeType: 'application/zip',
    buffer: archive(),
  })
  await page.getByTestId('import-preview').click()
  await expect(page.getByTestId('import-source')).toBeVisible()
}

test('못 이은 상태가 적재를 막고, 짝을 지어 주면 열린다', async ({ page, consoleErrors }) => {
  await signInWithMfa(page)
  const key = uniqueKey('I')
  await createProject(page, key)
  await upload(page, key)

  // 1. 묶음이 말하는 것을 화면이 그대로 말한다.
  await expect(page.getByTestId('import-source')).toContainText('redmine')
  await expect(page.getByTestId('import-source')).toContainText('OPS')

  // 2. **`New` 를 못 이었으니 막힌다.** 이것이 이 화면의 존재 이유다.
  await expect(page.getByTestId('import-blocking')).toBeVisible()
  await expect(page.getByTestId('import-load')).toBeDisabled();

  // 3. 사람이 짝을 짓는다 → 미리 보기가 다시 돌고 단추가 열린다.
  await pickOption(page.getByLabel(/statuses · New/i), /^Open\b/)
  await expect(page.getByTestId('import-blocking')).toHaveCount(0)
  await expect(page.getByTestId('import-load')).toBeEnabled()

  expect(consoleErrors).toEqual([])
})

test('두 번 실어도 안 늘어나고, 못 옮긴 것을 접어 두지 않는다', async ({
  page,
  consoleErrors,
}) => {
  await signInWithMfa(page)
  const key = uniqueKey('I')
  await createProject(page, key)
  await upload(page, key)
  await pickOption(page.getByLabel(/statuses · New/i), /^Open\b/)
  await expect(page.getByTestId('import-load')).toBeEnabled()

  // ── 첫 번째 ──────────────────────────────────────────────────
  await page.getByTestId('import-load').click()
  await expect(page.getByTestId('import-loaded')).toBeVisible()
  await expect(page.getByTestId('import-issues-created')).toHaveText('3')
  await expect(page.getByTestId('import-issues-skipped')).toHaveText('0')

  // **못 옮긴 것이 접히지 않고 그 자리에 있다.** 없는 계정을 가리킨 작성자와
  // 묶음 밖을 가리킨 관계가 이름으로 남아야 한다.
  const unmoved = page.getByTestId('import-unmoved')
  await expect(unmoved).toContainText('R-2')
  await expect(unmoved).toContainText('R-99')

  // ── 두 번째: 같은 묶음을 그대로 다시 ─────────────────────────
  //
  // 적재가 끝나면 화면이 미리 보기를 다시 받는다. "이미 옮긴 것" 이 3 이
  // 되어야 한다 — 그것이 멱등의 열쇠가 살아 있다는 뜻이다.
  await expect(page.getByTestId('import-already-here')).toHaveText('3')

  await page.getByTestId('import-load').click()
  await expect(page.getByTestId('import-issues-created')).toHaveText('0')
  await expect(page.getByTestId('import-issues-skipped')).toHaveText('3')

  expect(consoleErrors).toEqual([])
})
