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
 * **두 언어판을 다 연다.** 한국어판은 뿌리, 영어판은 `/en/` 밑이다. 언어
 * 메뉴는 실제로 눌러서 **같은 쪽의 다른 언어판**에 닿는지 본다 — 메뉴가
 * 첫 화면으로 가거나, 테마의 JS 가 가로채 엉뚱한 데로 보내도 링크 자체는
 * 살아 있어서 다른 확인으로는 안 잡힌다.
 *
 * **공개 사이트와 같은 경로(`/Ieum/`) 밑에 띄워서 본다.** 언어 메뉴의 첫 화면
 * 링크와 404 쪽은 `/Ieum/en/` 처럼 경로를 통째로 적는다 — 사이트를 뿌리에
 * 띄우면 그 링크만 깨지고, 그건 공개 사이트에는 없는 실패다.
 *
 *   mkdir -p /tmp/pages && ln -sfn "$PWD/site" /tmp/pages/Ieum
 *   python3 -m http.server 8899 --bind 127.0.0.1 --directory /tmp/pages &
 *   node tools/check-site.mjs http://127.0.0.1:8899/Ieum
 *
 * `playwright` 가 필요하다. 없으면 건너뛴다고 말하고 0 으로 끝난다 — 문서를
 * 고치는 사람에게 브라우저를 받으라고 요구하지 않는다(CI 가 본다).
 */

import assert from 'node:assert/strict'

const BASE = (process.argv[2] ?? 'http://127.0.0.1:8899/Ieum').replace(/\/$/, '')

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

  // 띄운 경로가 공개 사이트와 같은가. 다르면 아래 언어 메뉴 확인이 엉뚱한
  // 이유로 실패하므로, 원인을 먼저 말한다.
  await open('/')
  const canonical = await page.locator('link[rel=canonical]').getAttribute('href')
  const served = new URL(`${BASE}/`).pathname
  assert.equal(
    served,
    new URL(canonical).pathname,
    `사이트를 ${new URL(canonical).pathname} 밑에 띄워야 한다(지금 ${served}) — 맨 위 설명 참고`,
  )

  // 첫 화면의 길잡이 카드. 확장이 빠지면 여기가 0 이 된다. 영어판도 같다.
  for (const path of ['/', '/en/']) {
    await open(path)
    const cards = await page.locator('.grid.cards a, .grid a').count()
    assert.ok(cards >= 4, `${path} 첫 화면 카드가 ${cards}개다 (md_in_html 확장을 확인)`)
  }

  // 경고 상자. `admonition` 이 빠지면 글자로만 남는다.
  for (const path of ['/install/', '/en/install/']) {
    await open(path)
    assert.ok(
      (await page.locator('.admonition').count()) >= 1,
      `${path} 에 경고 상자가 안 그려졌다 (admonition 확장을 확인)`,
    )
  }

  // 검색. 색인이 안 실리면 결과가 0 이다. 색인은 두 판이 하나를 같이 쓰므로
  // (`mkdocs.yml` 의 `search`), 영어판에서 찾으면 영어판 쪽이 나와야 한다.
  for (const [path, query, inside] of [
    ['/', 'SLA', '/'],
    ['/', '서비스데스크', '/'],
    ['/en/', 'SLA', '/en/'],
    ['/en/', 'backup', '/en/'],
  ]) {
    await open(path)
    await page.locator('input[name=query]').fill(query)
    const hits = page.locator('.md-search-result__link')
    await hits.first().waitFor({ timeout: 15_000 })
    const hrefs = await hits.evaluateAll((links) => links.map((a) => new URL(a.href).pathname))
    const prefix = new URL(`${BASE}${inside}`).pathname
    assert.ok(
      hrefs.some(
        (href) => href.startsWith(prefix) && (inside !== '/' || !href.startsWith(`${prefix}en/`)),
      ),
      `${path} 에서 "${query}" 를 찾으면 그 판의 쪽이 안 나온다 — ${hrefs.slice(0, 5).join(', ')}`,
    )
  }

  // 언어 메뉴. 머리줄의 메뉴를 펼쳐(마우스를 올려) 다른 언어를 누른다.
  // 같은 쪽의 그 언어판에 닿고, 그 쪽의 `<html lang>` 이 그 언어여야 한다.
  for (const [from, lang, to] of [
    ['/', 'en', '/en/'],
    ['/en/', 'ko', '/'],
    ['/install/', 'en', '/en/install/'],
    ['/en/install/', 'ko', '/install/'],
    ['/guide/search/', 'en', '/en/guide/search/'],
    ['/en/guide/search/', 'ko', '/guide/search/'],
  ]) {
    await open(from)
    await page.locator('.md-header__option .md-select').hover()
    await Promise.all([
      page.waitForURL((url) => url.pathname !== new URL(`${BASE}${from}`).pathname, {
        timeout: 10_000,
      }),
      page.locator(`.md-select__link[hreflang="${lang}"]`).click(),
    ])
    assert.equal(
      new URL(page.url()).pathname,
      new URL(`${BASE}${to}`).pathname,
      `${from} 에서 언어 메뉴의 ${lang} 을 누르면 ${to} 로 가야 한다`,
    )
    assert.equal(
      await page.locator('html').getAttribute('lang'),
      lang,
      `${to} 의 <html lang> 이 ${lang} 이 아니다`,
    )
  }

  // 어두운 화면 토글. 고른 색은 브라우저에 남으므로 쪽마다 밝은 화면에서
  // 시작하도록 지운다.
  for (const path of ['/guide/', '/en/guide/']) {
    await open(path)
    await page.evaluate(() => localStorage.clear())
    await open(path)
    await page.locator('label[for="__palette_1"]').click()
    await page.waitForTimeout(400)
    assert.equal(
      await page.locator('body').getAttribute('data-md-color-scheme'),
      'slate',
      `${path} 에서 어두운 화면 토글이 안 듣는다`,
    )
  }

  // 좁은 화면에서 가로로 넘치지 않는가. 표가 많은 문서가 특히 위험하다.
  // 영어는 낱말이 길어 표 칸이 더 넓어지므로 영어판도 본다. **열린 쪽이
  // 200 인지 본다** — 없는 주소면 서버의 오류 쪽이 열리고, 그 쪽은 안 넘치므로
  // 확인이 조용히 통과한다(예전에 없는 주소 하나가 그렇게 섞여 있었다).
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const small = await phone.newPage()
  small.on('request', (r) => {
    const target = new URL(r.url()).host
    if (target && target !== host) outside.add(target)
  })
  for (const path of [
    '/',
    '/install/',
    '/guide/search/',
    '/operations/',
    '/en/',
    '/en/install/',
    '/en/guide/search/',
    '/en/operations/',
  ]) {
    const response = await small.goto(BASE + path, { waitUntil: 'domcontentloaded' })
    assert.equal(response.status(), 200, `${path} 가 ${response.status()}`)
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
