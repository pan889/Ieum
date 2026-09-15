/**
 * 빌드된 문서 사이트를 **실제 브라우저로** 몰아 본다.
 *
 * `mkdocs build --strict` 는 링크와 nav 를 잡지만 **그려지는지는 모른다.**
 * 이걸 붙이면서 찾은 것 둘이 그 증거다.
 *
 * 1. 첫 화면의 카드가 링크 0개였다 — `md_in_html` 확장을 안 켜서 `<div
 *    markdown>` 안이 마크다운으로 안 읽혔다. 빌드는 통과했다.
 * 2. 페이지를 열 때마다 `fonts.googleapis.com` 과 `api.github.com` 을
 *    불렀다 — Material 기본값이다. "외부 SaaS 없이 완전 동작" 을 말하는
 *    제품의 문서가 방문자마다 밖에 알리고 있었고, 폐쇄망에서는 그냥 안 되는
 *    요청이다.
 *
 * 2번이 이 파일을 계속 둘 이유다. 테마를 올릴 때 되살아나기 쉽고, 되살아나도
 * 화면은 멀쩡해 보인다.
 *
 *   node tools/check-site.mjs [http://127.0.0.1:8899]
 *
 * `playwright` 가 필요하다. 없으면 건너뛴다고 말하고 0 으로 끝난다 — 문서를
 * 고치는 사람에게 브라우저를 받으라고 요구하지 않는다(CI 가 본다).
 */

import assert from 'node:assert/strict'

const BASE = process.argv[2] ?? 'http://127.0.0.1:8899'

// `playwright` 든 `@playwright/test` 든 있으면 쓴다. 코드 저장소에는
// 후자가 있고 CI 는 전자를 넣는다 — 이름 하나 때문에 못 돌리게 하지 않는다.
let chromium
for (const name of ['playwright', '@playwright/test']) {
  try {
    ;({ chromium } = await import(name))
    break
  } catch {
    // 다음 이름을 본다.
  }
}
if (!chromium) {
  // CI 는 건너뛰기를 허락하지 않는다. `CHECK_SITE_REQUIRED=1` 없이 두면
  // 브라우저 준비가 조용히 실패해도 초록으로 끝난다 — 이 저장소에서 이미
  // 한 번 겪은 종류의 구멍이다(스킵을 성공으로 셌다).
  if (process.env.CHECK_SITE_REQUIRED) {
    console.error('playwright 를 못 불렀다 — CHECK_SITE_REQUIRED 가 켜져 있어 건너뛰지 않는다')
    process.exit(1)
  }
  console.log('playwright 가 없다 — 건너뛴다 (CI 가 본다)')
  process.exit(0)
}

const browser = await chromium.launch(
  process.env.CHECK_SITE_CHROMIUM ? { executablePath: process.env.CHECK_SITE_CHROMIUM } : {},
)
const problems = []
/** 우리 사이트가 아닌 곳으로 나간 요청. 하나도 없어야 한다. */
const outside = new Set()
const host = new URL(BASE).host

try {
  const page = await browser.newPage()
  page.on('request', (r) => {
    const target = new URL(r.url()).host
    if (target && target !== host) outside.add(target)
  })
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`콘솔: ${page.url()} :: ${m.text()}`)
  })
  page.on('pageerror', (e) => problems.push(`스크립트: ${page.url()} :: ${e.message}`))

  async function open(path) {
    const response = await page.goto(BASE + path, { waitUntil: 'load' })
    assert.equal(response.status(), 200, `${path} 가 ${response.status()}`)
  }

  // 첫 화면의 길잡이 카드. 확장이 빠지면 여기가 0 이 된다.
  await open('/')
  const cards = await page.locator('.grid.cards a, .grid a').count()
  assert.ok(cards >= 4, `첫 화면 카드가 ${cards}개다 (md_in_html 확장을 확인)`)

  // 경고 상자. `admonition` 이 빠지면 글자로만 남는다.
  await open('/install/')
  assert.ok(
    (await page.locator('.admonition').count()) >= 1,
    '경고 상자가 안 그려졌다 (admonition 확장을 확인)',
  )

  // 검색. 색인이 안 실리면 결과가 0 이다.
  await open('/')
  await page.locator('input[name=query]').fill('SLA')
  const hits = page.locator('.md-search-result__link')
  await hits.first().waitFor({ timeout: 15_000 })
  assert.ok((await hits.count()) >= 2, '검색 결과가 없다')

  // 어두운 화면 토글.
  await open('/guide/')
  await page.locator('label[for="__palette_1"]').click()
  await page.waitForTimeout(400)
  assert.equal(
    await page.locator('body').getAttribute('data-md-color-scheme'),
    'slate',
    '어두운 화면 토글이 안 듣는다',
  )

  // 좁은 화면에서 가로로 넘치지 않는가. 표가 많은 문서가 특히 위험하다.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const small = await phone.newPage()
  for (const path of ['/', '/install/', '/guide/search/', '/contributing/operations/']) {
    await small.goto(BASE + path, { waitUntil: 'domcontentloaded' })
    const over = await small.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    assert.ok(over <= 1, `${path} 가 좁은 화면에서 ${over}px 넘친다`)
  }
} finally {
  await browser.close()
}

if (outside.size) problems.push(`밖으로 나간 요청: ${[...outside].join(', ')}`)

if (problems.length) {
  console.error('문서 사이트 확인 실패:')
  for (const line of problems) console.error(`  - ${line}`)
  process.exit(1)
}
console.log('문서 사이트 확인 통과 — 밖으로 나간 요청 없음')
