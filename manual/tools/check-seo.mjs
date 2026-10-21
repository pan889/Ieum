/**
 * 빌드된 사이트가 **검색에 걸릴 모양을 갖췄는지** 본다.
 *
 * 왜 게이트로 두는가: 이 저장소는 "설정에 이름만 적혀 있고 실제로는 안
 * 실리는 것" 에 두 번 당했다(다크 모드 팔레트, 한글 글꼴). SEO 머리말은
 * 정확히 같은 성질이다 — `custom_dir: overrides` 한 줄을 지우면 og 태그가
 * 통째로 사라지는데 **화면은 아무렇지도 않다.** 사람은 몇 달 뒤 검색 결과가
 * 이상한 것을 보고서야 안다.
 *
 * **두 언어판을 다 본다.** 한국어판이 뿌리, 영어판이 `en/` 밑이다. 언어판은
 * 설정이 아니라 결과물에서 찾는다 — 폴더 이름이 그 첫 화면의 `<html lang>` 과
 * 같으면 언어판이다. 언어 때문에 더 보는 것:
 *
 * - `<html lang>`·`og:locale`·JSON-LD `inLanguage` 가 그 판의 언어인가.
 *   영어 번역(`*.en.md`)이 없는 쪽은 i18n 플러그인이 한국어 원본으로 채운다 —
 *   빌드는 통과하고 화면도 멀쩡하지만, 영어판 주소에 `lang="en"` 을 달고
 *   한국어 글이 나간다. **여기서 실패로 잡는다.**
 * - og 그림·canonical 이 그 판 것인가(영어판 링크에 한국어 카드가 뜨지 않게).
 * - 영어판의 머리줄·목차·바닥글에 한국어가 남지 않았는가(`nav_translations` 를
 *   빠뜨리면 목차 한 줄만 한국어로 남는다).
 * - hreflang. **사이트맵에만 있어야 한다** — 머리말에 다시 생기면 테마의 JS 가
 *   그 주소마다 `sitemap.xml` 을 받으러 가서 쪽마다 404 가 난다
 *   (`overrides/main.html` 의 `site_meta`). 사이트맵의 hreflang 은 서로를
 *   가리켜야 하고(한쪽만 가리키면 검색 엔진이 버린다), 각 주소가 그 언어판에
 *   있어야 하고, `x-default` 가 있어야 한다.
 * - 언어 메뉴가 사이트맵이 말하는 **같은 쪽의 다른 언어판**으로 가는가.
 * - 뿌리의 `404.html` 이 기본 언어인가(영어판 빌드가 덮어쓰면 영어가 된다).
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

const htmlLang = (html) => /<html[^>]*\slang=["']([^"']+)["']/i.exec(html)?.[1] ?? null
const canonicalOf = (html) =>
  /<link[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']/i.exec(html)?.[1] ?? null
const HANGUL = /[가-힣]/

// ── 언어판 ────────────────────────────────────────────────────────
//
// 뿌리 `index.html` 의 언어가 기본 언어다. 그 밑의 폴더 중 첫 화면의 언어가
// 폴더 이름과 같은 것이 다른 언어판이다(`en/index.html` 이 `lang="en"`).
let home = ''
try {
  home = await readFile(join(SITE, 'index.html'), 'utf-8')
} catch {
  fail(SITE, '첫 화면(index.html)이 없다 — 빌드를 먼저 해야 한다')
}
const defaultLang = htmlLang(home)
/** 사이트의 공개 주소. 뿌리 첫 화면의 canonical 이다(`https://…/Ieum/`). */
const siteUrl = canonicalOf(home) ?? ''
/** 폴더 → 언어. 기본 언어는 `''` 이다. */
const roots = new Map([['', defaultLang]])
for (const entry of await readdir(SITE, { withFileTypes: true }).catch(() => [])) {
  if (!entry.isDirectory()) continue
  try {
    const lang = htmlLang(await readFile(join(SITE, entry.name, 'index.html'), 'utf-8'))
    if (lang === entry.name) roots.set(entry.name, lang)
  } catch {
    // 첫 화면이 없는 폴더(assets, search …)는 언어판이 아니다.
  }
}

/** 결과물 안의 경로(`en/install/index.html`)가 어느 언어판인가. */
function languageOfPath(rel) {
  const first = rel.split(sep)[0]
  return roots.has(first) && first !== '' ? roots.get(first) : defaultLang
}

/** 공개 주소가 어느 언어판인가. 사이트 밖이면 null. */
function languageOfUrl(url) {
  if (!siteUrl || !url.startsWith(siteUrl)) return null
  const first = url.slice(siteUrl.length).split('/')[0]
  return roots.has(first) && first !== '' ? roots.get(first) : defaultLang
}

/** 결과물 경로 → 쪽 이름(`install`, `guide/search`). 실패 문구에 무엇을 번역하면 되는지 적으려고. */
function pageName(rel, lang) {
  const inside = rel
    .split(sep)
    .slice(lang === defaultLang ? 0 : 1)
    .join('/')
  return inside.replace(/(^|\/)index\.html$/, '') || '첫 화면(index)'
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
/** loc → { hreflang → href } */
const entries = new Map()
for (const [, body] of sitemap.matchAll(/<url>([\s\S]*?)<\/url>/g)) {
  const loc = /<loc>([^<]+)<\/loc>/.exec(body)?.[1]
  if (!loc) continue
  const alternates = new Map()
  for (const [tag] of body.matchAll(/<xhtml:link[^>]*>/g)) {
    const lang = /hreflang="([^"]+)"/.exec(tag)?.[1]
    const href = /href="([^"]+)"/.exec(tag)?.[1]
    if (lang && href) alternates.set(lang, href)
  }
  entries.set(loc, alternates)
}
const listed = new Set(entries.keys())

// ── robots.txt ────────────────────────────────────────────────────
try {
  const robots = await readFile(join(SITE, 'robots.txt'), 'utf-8')
  if (/^\s*Disallow:\s*\/\s*$/im.test(robots)) fail('robots.txt', '사이트 전체를 막고 있다')
  if (!/^\s*Sitemap:\s*\S+/im.test(robots)) fail('robots.txt', 'Sitemap 줄이 없다')
} catch {
  fail('robots.txt', '없다')
}

// ── 404 ───────────────────────────────────────────────────────────
//
// GitHub Pages 는 뿌리의 `404.html` 하나만 쓴다. 영어판 빌드가 그것을 덮어쓰면
// 한국어판에서 길을 잃은 사람이 영어 쪽을 본다(`overrides/404.html`).
try {
  const notFound = await readFile(join(SITE, '404.html'), 'utf-8')
  if (htmlLang(notFound) !== defaultLang) {
    fail(
      '404.html',
      `기본 언어(${defaultLang})가 아니다(${htmlLang(notFound)}) — 다른 언어판 빌드가 덮어썼다`,
    )
  }
} catch {
  fail('404.html', '없다')
}

// ── 쪽마다 ────────────────────────────────────────────────────────
const descriptions = new Map()
const canonicals = new Set()
/** 원본으로 떨어진 쪽. 사이트맵·메뉴 확인에서 같은 원인을 두 번 말하지 않으려고. */
const fallbacks = new Set()
/** canonical → 언어 메뉴의 { hreflang → 절대 주소 } */
const menus = new Map()

for (const file of pages) {
  const where = relative(SITE, file)
  const html = await readFile(file, 'utf-8')
  const headEnd = html.indexOf('</head>')
  const head = headEnd >= 0 ? html.slice(0, headEnd) : html
  const expected = languageOfPath(where)

  const lang = htmlLang(html)
  if (lang !== expected) fail(where, `<html lang="${expected}"> 가 아니다 (${lang})`)

  // **글의 언어.** og:locale 은 판이 아니라 실제로 쓰인 언어를 말한다
  // (`overrides/main.html`). 판 언어와 다르면 번역이 없어 원본으로 떨어진 쪽이다.
  const locale = metas(html, 'property', 'og:locale')[0] ?? ''
  const fellBack = locale !== '' && !locale.startsWith(`${expected}_`)
  if (fellBack) {
    fallbacks.add(where)
    fail(
      where,
      `${expected} 판인데 글이 ${locale} 이다 — 번역이 없어 원본으로 떨어졌다. ` +
        `docs/ 에서 ${pageName(where, expected)} 옆에 .${expected}.md 를 만든다`,
    )
  }
  one(html, 'property', 'og:locale', where)

  if (!fellBack && /<meta[^>]*name=["']robots["'][^>]*noindex/i.test(head)) {
    fail(where, '색인하지 말라고(noindex) 적혀 있다')
  }

  // hreflang 은 사이트맵만 갖는다. 위 머리말 설명 참고.
  const headHreflang = [...head.matchAll(/<link[^>]*hreflang=["'][^"']+["'][^>]*>/gi)]
  if (headHreflang.length > 0) {
    fail(
      where,
      `머리말에 hreflang 이 ${headHreflang.length}줄 있다 — 테마 JS 가 그 주소마다 ` +
        'sitemap.xml 을 받으러 가 404 가 난다. hreflang 은 사이트맵이 맡는다(overrides/main.html)',
    )
  }

  const canonical = canonicalOf(html)
  if (!canonical) fail(where, 'canonical 이 없다')
  else {
    canonicals.add(canonical)
    if (languageOfUrl(canonical) !== expected) {
      fail(where, `canonical 이 ${expected} 판 주소가 아니다 — ${canonical}`)
    }
    // 원본으로 떨어진 쪽은 사이트맵에 일부러 안 싣는다(noindex 라서). 위에서 이미 말했다.
    if (listed.size > 0 && !listed.has(canonical) && !fellBack) {
      fail(where, `사이트맵에 이 주소가 없다 — ${canonical}`)
    }
  }

  const description = one(html, 'name', 'description', where)
  // 원본으로 떨어진 쪽은 원본과 설명이 같을 수밖에 없다 — 위에서 이미 말했다.
  if (description && !fellBack) {
    // **같은 설명을 두 쪽이 쓰면 검색 결과에서 구별이 안 된다.** 대개는
    // 머리말을 안 단 쪽이 사이트 기본 설명을 물려받은 것이다. 두 판 사이에서
    // 같으면 번역하면서 front matter 를 빠뜨린 것이다.
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
  if (canonical && ogUrl && ogUrl !== canonical) {
    fail(where, `og:url 과 canonical 이 다르다 — ${ogUrl} vs ${canonical}`)
  }

  // **그림이 진짜로 있어야 한다.** 없는 주소를 og:image 로 두면 링크를
  // 붙였을 때 빈 카드가 뜨고, 그건 안 붙인 것보다 나쁘다. 그리고 **그 판의
  // 그림이어야 한다** — 카드에 글이 적혀 있다.
  const ogImage = metas(html, 'property', 'og:image')[0]
  if (ogImage) {
    if (languageOfUrl(ogImage) !== expected) {
      fail(where, `og:image 가 ${expected} 판 그림이 아니다 — ${ogImage}`)
    }
    const path = siteUrl && ogImage.startsWith(siteUrl) ? ogImage.slice(siteUrl.length) : ogImage
    try {
      await access(join(SITE, path))
    } catch {
      fail(where, `og:image 가 가리키는 파일이 없다 — ${path}`)
    }
  }

  const ld = [
    ...html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi),
  ]
  if (ld.length === 0) fail(where, '구조화 데이터(JSON-LD)가 없다')
  for (const [, body] of ld) {
    try {
      const data = JSON.parse(body)
      if (!fellBack && data.inLanguage !== expected) {
        fail(where, `JSON-LD 의 inLanguage 가 ${expected} 가 아니다 (${data.inLanguage})`)
      }
    } catch (error) {
      // 깨진 JSON-LD 는 **조용히 무시된다.** 구글은 오류를 안 알려 준다.
      fail(where, `JSON-LD 가 JSON 이 아니다 — ${error.message}`)
    }
  }

  // 다른 언어판의 **머리줄·목차·바닥글**에 한국어가 남았는가. 본문은 안
  // 본다 — 프로그램 출력처럼 일부러 남긴 한국어가 있다. 언어 메뉴의
  // "한국어" 는 그 언어로 적는 것이 맞으므로 뺀다. 글자뿐 아니라 사람에게
  // 읽히는 속성(title·aria-label·placeholder·alt)과 머리말의 설명도 본다.
  if (expected !== 'ko' && !fellBack) {
    const chrome = html
      .replace(/<article\b[\s\S]*?<\/article>/gi, ' ')
      .replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
      .replace(/<a\b[^>]*class="md-select__link"[^>]*>[\s\S]*?<\/a>/gi, ' ')
      .replace(/<meta[^>]*name=["'](?:google|naver)-site-verification["'][^>]*>/gi, ' ')
    const readable = [
      ...chrome.replace(/<[^>]*>/g, '\n').split('\n'),
      ...[...chrome.matchAll(/\s(?:title|aria-label|placeholder|alt|content)="([^"]*)"/gi)].map(
        (m) => m[1],
      ),
    ]
    const left = [...new Set(readable.map((s) => s.trim()).filter((s) => HANGUL.test(s)))]
    if (left.length > 0) {
      fail(
        where,
        `${expected} 판의 머리줄·목차·바닥글에 한국어가 남았다 — ${left
          .slice(0, 3)
          .map((s) => `"${s.slice(0, 40)}"`)
          .join(', ')}`,
      )
    }
  }

  // 언어 메뉴. 쪽 주소를 기준으로 풀어 둔다 — 아래에서 사이트맵과 견준다.
  if (canonical) {
    const menu = new Map()
    for (const [tag] of html.matchAll(/<a\b[^>]*class="md-select__link"[^>]*>/gi)) {
      const lang = /hreflang="([^"]+)"/.exec(tag)?.[1]
      const href = /href="([^"]+)"/.exec(tag)?.[1]
      if (lang && href) menu.set(lang, new URL(href, canonical).href)
    }
    menus.set(canonical, { where, menu, fellBack })
  }
}

// ── hreflang (사이트맵) ───────────────────────────────────────────
//
// 구글은 **서로 가리키지 않는** hreflang 을 버린다. 그리고 주소가 그 언어판에
// 있어야 한다 — `hreflang="en"` 이 한국어 쪽을 가리키면 영어로 찾은 사람에게
// 한국어를 내민다.
const languages = [...new Set(roots.values())]
let pairs = 0
// 언어판이 하나면 hreflang 이 필요 없다.
for (const [loc, alternates] of languages.length > 1 ? entries : []) {
  const own = languageOfUrl(loc)
  if (alternates.get(own) !== loc)
    fail('sitemap.xml', `${loc} 의 hreflang 에 자기 자신(${own})이 없다`)
  for (const lang of languages) {
    if (!alternates.has(lang)) fail('sitemap.xml', `${loc} 에 ${lang} 판이 없다 (hreflang)`)
  }
  if (!alternates.has('x-default')) fail('sitemap.xml', `${loc} 에 x-default 가 없다`)
  else if (
    ![...alternates].some(
      ([lang, href]) => lang !== 'x-default' && href === alternates.get('x-default'),
    )
  ) {
    fail(
      'sitemap.xml',
      `${loc} 의 x-default 가 언어판 중 하나가 아니다 — ${alternates.get('x-default')}`,
    )
  }
  for (const [lang, href] of alternates) {
    if (lang === 'x-default') continue
    pairs += 1
    if (languageOfUrl(href) !== lang)
      fail('sitemap.xml', `${loc} 의 hreflang="${lang}" 이 ${lang} 판 주소가 아니다 — ${href}`)
    const back = entries.get(href)
    if (!back) {
      fail('sitemap.xml', `${loc} 의 hreflang="${lang}" 이 사이트맵에 없는 쪽을 가리킨다 — ${href}`)
      continue
    }
    const mine = JSON.stringify([...alternates].sort())
    const theirs = JSON.stringify([...back].sort())
    if (mine !== theirs)
      fail('sitemap.xml', `${loc} 와 ${href} 의 hreflang 이 서로 다르다 — 서로 가리켜야 한다`)
  }

  // 언어 메뉴가 같은 쪽의 다른 언어판으로 가는가. 사이트맵이 정답이다.
  const page = menus.get(loc)
  if (page && languages.length > 1) {
    for (const lang of languages) {
      const want = alternates.get(lang)
      const got = page.menu.get(lang)
      if (!got) fail(page.where, `언어 메뉴에 ${lang} 이 없다`)
      else if (want && got !== want)
        fail(page.where, `언어 메뉴의 ${lang} 이 ${got} 로 간다 — ${want} 여야 한다`)
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
const perLanguage = languages
  .map(
    (lang) => `${lang} ${pages.filter((p) => languageOfPath(relative(SITE, p)) === lang).length}`,
  )
  .join(', ')
console.log(
  `검색 준비 확인 통과 — 쪽 ${pages.length}개(${perLanguage}), 사이트맵 ${listed.size}줄, hreflang ${pairs}쌍`,
)
