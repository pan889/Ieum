/**
 * 빌드된 사이트가 **검색에 걸릴 모양을 갖췄는지** 본다.
 *
 * 왜 게이트로 두는가: 이 저장소는 "설정에 이름만 적혀 있고 실제로는 안
 * 실리는 것" 에 두 번 당했다(다크 모드 팔레트, 한글 글꼴). SEO 머리말은
 * 정확히 같은 성질이다 — `custom_dir: overrides` 한 줄을 지우면 og 태그가
 * 통째로 사라지는데 **화면은 아무렇지도 않다.** 사람은 몇 달 뒤 검색 결과가
 * 이상한 것을 보고서야 안다.
 *
 *   node tools/check-seo.mjs [site]
 *
 * 브라우저를 안 쓴다. 검색 로봇이 받는 것은 정적 HTML 이고, 우리가 볼 것도
 * 그것이다.
 */

import { readFile, readdir, access } from 'node:fs/promises'
import { join, relative, sep } from 'node:path'

const SITE = process.argv[2] ?? 'site'
const problems = []

function fail(where, what) {
  problems.push(`${where}: ${what}`)
}

/** `<meta>` 한 종류의 content 를 전부 모은다. 개수까지 봐야 중복을 잡는다. */
function metas(html, attr, name) {
  const out = []
  const re = new RegExp(`<meta[^>]*${attr}=["']${name}["'][^>]*>`, 'gi')
  for (const [tag] of html.matchAll(re)) {
    const content = /content=["']([^"']*)["']/i.exec(tag)
    out.push(content ? content[1] : '')
  }
  return out
}

function one(html, attr, name, where) {
  const found = metas(html, attr, name)
  if (found.length === 0) {
    fail(where, `<meta ${attr}="${name}"> 이 없다`)
    return null
  }
  if (found.length > 1) {
    // 둘이 있으면 어느 것이 이기는지 사람이 모른다. 테마가 이미 내주는 것을
    // 우리가 또 쓴 상황이고, 그건 고쳐야 할 결함이다.
    fail(where, `<meta ${attr}="${name}"> 이 ${found.length}개다 — 하나여야 한다`)
  }
  if (!found[0]) fail(where, `<meta ${attr}="${name}"> 의 content 가 비었다`)
  return found[0]
}

async function htmlFiles(dir) {
  const out = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...(await htmlFiles(path)))
    else if (entry.name.endsWith('.html')) out.push(path)
  }
  return out
}

const pages = (await htmlFiles(SITE)).filter((p) => !p.endsWith(`${sep}404.html`))
if (pages.length === 0) fail(SITE, 'HTML 이 하나도 없다 — 빌드를 먼저 해야 한다')

// ── 사이트맵 ──────────────────────────────────────────────────────
//
// 사이트맵이 실제 쪽과 어긋나면, 새로 쓴 문서를 구글이 **영영 모른다.**
let sitemap = ''
try {
  sitemap = await readFile(join(SITE, 'sitemap.xml'), 'utf-8')
} catch {
  fail('sitemap.xml', '없다')
}
const listed = new Set([...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]))

// ── robots.txt ────────────────────────────────────────────────────
try {
  const robots = await readFile(join(SITE, 'robots.txt'), 'utf-8')
  if (/^\s*Disallow:\s*\/\s*$/im.test(robots)) fail('robots.txt', '사이트 전체를 막고 있다')
  if (!/^\s*Sitemap:\s*\S+/im.test(robots)) fail('robots.txt', 'Sitemap 줄이 없다')
} catch {
  fail('robots.txt', '없다')
}

// ── 쪽마다 ────────────────────────────────────────────────────────
const descriptions = new Map()
const canonicals = new Set()

for (const file of pages) {
  const where = relative(SITE, file)
  const html = await readFile(file, 'utf-8')

  if (!/<html[^>]*\slang=["']ko["']/i.test(html)) fail(where, '<html lang="ko"> 가 아니다')

  const canonical = /<link[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']/i.exec(html)
  if (!canonical) fail(where, 'canonical 이 없다')
  else {
    canonicals.add(canonical[1])
    if (listed.size > 0 && !listed.has(canonical[1])) {
      fail(where, `사이트맵에 이 주소가 없다 — ${canonical[1]}`)
    }
  }

  const description = one(html, 'name', 'description', where)
  if (description) {
    // **같은 설명을 두 쪽이 쓰면 검색 결과에서 구별이 안 된다.** 대개는
    // 머리말을 안 단 쪽이 사이트 기본 설명을 물려받은 것이다.
    const seen = descriptions.get(description)
    if (seen) fail(where, `설명이 ${seen} 와 똑같다 — 쪽마다 달라야 한다`)
    else descriptions.set(description, where)
    if (description.length < 40) fail(where, `설명이 너무 짧다(${description.length}자)`)
  }

  for (const [attr, name] of [
    ['property', 'og:title'],
    ['property', 'og:description'],
    ['property', 'og:url'],
    ['property', 'og:image'],
    ['name', 'twitter:card'],
  ]) {
    one(html, attr, name, where)
  }

  const ogUrl = metas(html, 'property', 'og:url')[0]
  if (canonical && ogUrl && ogUrl !== canonical[1]) {
    fail(where, `og:url 과 canonical 이 다르다 — ${ogUrl} vs ${canonical[1]}`)
  }

  // **그림이 진짜로 있어야 한다.** 없는 주소를 og:image 로 두면 링크를
  // 붙였을 때 빈 카드가 뜨고, 그건 안 붙인 것보다 나쁘다.
  const ogImage = metas(html, 'property', 'og:image')[0]
  if (ogImage) {
    const path = ogImage.replace(/^https?:\/\/[^/]+/, '').replace(/^\/[^/]+\//, '')
    try {
      await access(join(SITE, path))
    } catch {
      fail(where, `og:image 가 가리키는 파일이 없다 — ${path}`)
    }
  }

  const ld = [...html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)]
  if (ld.length === 0) fail(where, '구조화 데이터(JSON-LD)가 없다')
  for (const [, body] of ld) {
    try {
      JSON.parse(body)
    } catch (error) {
      // 깨진 JSON-LD 는 **조용히 무시된다.** 구글은 오류를 안 알려 준다.
      fail(where, `JSON-LD 가 JSON 이 아니다 — ${error.message}`)
    }
  }
}

// 사이트맵에 있는데 실제로 없는 쪽. 지운 문서가 사이트맵에 남으면 404 를
// 크롤링하게 된다.
for (const loc of listed) {
  if (!canonicals.has(loc)) fail('sitemap.xml', `없는 쪽을 가리킨다 — ${loc}`)
}

if (problems.length > 0) {
  console.error('검색 준비 확인 실패:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`검색 준비 확인 통과 — 쪽 ${pages.length}개, 사이트맵 ${listed.size}줄`)
