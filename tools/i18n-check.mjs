#!/usr/bin/env node
/**
 * i18n CI 게이트 (docs/architecture/i18n.md 4절).
 *
 * 이 게이트가 없으면 en/ko 동시 지원은 2주 안에 무너진다. 네 가지를 본다:
 *
 *   1. 키 동기화   — en 과 ko 의 키 집합이 완전히 일치 (누락·잉여 모두 실패)
 *   2. ICU 문법    — 두 언어 모두 파싱 가능
 *   3. 플레이스홀더 — 두 언어의 변수 집합이 일치
 *   4. 하드코딩     — 번역 함수를 거치지 않은 문장 리터럴 (실패)
 *   5. 미사용 키    — 경고만 (기능보다 카탈로그가 먼저 오는 경우가 있다)
 */
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs'
import { join, relative, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse } from '@formatjs/icu-messageformat-parser'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const CATALOG_DIR = join(ROOT, 'packages/i18n')
const SOURCE_ROOTS = [join(ROOT, 'apps/web/src')]
// 키를 **쓰는** 곳은 화면보다 넓다. `errors:${code}` 는 api-client 안에 있고,
// 알림 제목은 서버가 같은 카탈로그로 렌더한다(i18n.md 3절). 여기를 빼 두면
// 멀쩡히 쓰이는 키가 "죽었다" 고 보고된다.
const USAGE_ROOTS = [
  ...SOURCE_ROOTS,
  join(ROOT, 'packages/api-client/src'),
  join(ROOT, 'apps/api/src'),
]
const USAGE_EXTENSIONS = ['.ts', '.tsx', '.py']
const SOURCE_LOCALE = 'en'

const problems = []
const warnings = []
const fail = (m) => problems.push(m)
const warn = (m) => warnings.push(m)

// ── 카탈로그 로드 ────────────────────────────────────────────────
function locales() {
  return readdirSync(CATALOG_DIR).filter(
    (d) => statSync(join(CATALOG_DIR, d)).isDirectory() && /^[a-z]{2}(-[A-Z]{2})?$/.test(d),
  )
}

function loadCatalog(locale) {
  const dir = join(CATALOG_DIR, locale)
  const out = new Map()
  for (const file of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
    const ns = file.replace(/\.json$/, '')
    let json
    try {
      json = JSON.parse(readFileSync(join(dir, file), 'utf8'))
    } catch (err) {
      fail(`${locale}/${file}: JSON 파싱 실패 — ${err.message}`)
      continue
    }
    for (const [key, value] of Object.entries(json)) {
      if (typeof value !== 'string') {
        fail(`${locale}/${file}: '${key}' 값이 문자열이 아니다 (중첩 객체 금지)`)
        continue
      }
      out.set(`${ns}:${key}`, value)
    }
  }
  return out
}

// ── 1. 키 동기화 ─────────────────────────────────────────────────
function checkKeySync(catalogs) {
  const source = catalogs.get(SOURCE_LOCALE)
  if (!source) {
    fail(`원본 언어 '${SOURCE_LOCALE}' 카탈로그가 없다`)
    return
  }
  for (const [locale, catalog] of catalogs) {
    if (locale === SOURCE_LOCALE) continue
    const missing = [...source.keys()].filter((k) => !catalog.has(k))
    const extra = [...catalog.keys()].filter((k) => !source.has(k))
    if (missing.length) {
      fail(`${locale}: ${missing.length}개 키 누락 — ${missing.slice(0, 5).join(', ')}${missing.length > 5 ? ' …' : ''}`)
    }
    if (extra.length) {
      fail(`${locale}: ${SOURCE_LOCALE} 에 없는 키 ${extra.length}개 — ${extra.slice(0, 5).join(', ')}${extra.length > 5 ? ' …' : ''}`)
    }
  }
}

// ── 2·3. ICU 문법과 플레이스홀더 ─────────────────────────────────
function placeholders(ast, into = new Set()) {
  for (const node of ast) {
    if (node.value !== undefined && node.type !== 0) into.add(node.value)
    if (node.options) {
      for (const opt of Object.values(node.options)) placeholders(opt.value, into)
    }
    if (node.children) placeholders(node.children, into)
  }
  return into
}

function checkIcu(catalogs) {
  const parsed = new Map()
  for (const [locale, catalog] of catalogs) {
    const byKey = new Map()
    for (const [key, message] of catalog) {
      try {
        byKey.set(key, placeholders(parse(message)))
      } catch (err) {
        fail(`${locale} '${key}': ICU 문법 오류 — ${err.message.split('\n')[0]}`)
      }
    }
    parsed.set(locale, byKey)
  }

  const source = parsed.get(SOURCE_LOCALE)
  if (!source) return
  for (const [locale, byKey] of parsed) {
    if (locale === SOURCE_LOCALE) continue
    for (const [key, vars] of byKey) {
      const expected = source.get(key)
      if (!expected) continue
      const missing = [...expected].filter((v) => !vars.has(v))
      const extra = [...vars].filter((v) => !expected.has(v))
      if (missing.length || extra.length) {
        fail(
          `${locale} '${key}': 플레이스홀더 불일치 ` +
            `(누락: ${missing.join(', ') || '없음'} / 잉여: ${extra.join(', ') || '없음'})`,
        )
      }
    }
  }
}

// ── 4. 하드코딩 탐지 ─────────────────────────────────────────────
const TRANSLATED = /\b(?:t|i18n\.t|useTranslation)\s*\(/
// 문장으로 보이는 리터럴: 한글이 있거나, 공백 포함 영문 2단어 이상
const SENTENCE = /^[^\S\n]*(?:[가-힣][^"'`]*|[A-Z][a-z]+(?:\s+[A-Za-z,'’-]+){1,})[.!?]?[^\S\n]*$/
const ALLOWED_ATTRS = /\b(?:className|class|key|id|type|name|href|src|role|data-[\w-]+|aria-[\w-]+|to|path|charSet|rel|target|method|autoComplete|inputMode|pattern|placeholder=\{)\s*=/

function walk(dir, acc = [], extensions = ['.ts', '.tsx']) {
  if (!existsSync(dir)) return acc
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__pycache__' || entry.startsWith('.')) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, acc, extensions)
    else if (
      extensions.includes(extname(entry)) &&
      !entry.endsWith('.d.ts') &&
      // 테스트 설명문은 사용자에게 보이지 않는다. 번역 대상이 아니다.
      !/\.(test|spec)\.tsx?$/.test(entry)
    ) {
      acc.push(full)
    }
  }
  return acc
}

/**
 * 주석을 지운다. 줄 번호는 유지한다(개행만 남긴다).
 *
 * 주석은 사용자에게 안 보이므로 번역 대상이 아니다. 줄 단위로 `//` 만 걸러
 * 내면 여러 줄 블록 주석의 가운데 줄이 걸린다 — 그러면 UI 문구를 인용하는
 * 주석을 못 쓰게 되고, 결국 주석이 나빠진다.
 *
 * 문자열·템플릿 리터럴 안의 `/*` 를 주석으로 오해하지 않도록 상태를 따라간다.
 */
function stripComments(source) {
  let out = ''
  let i = 0
  const keepNewlines = (text) => text.replace(/[^\n]/g, ' ')

  while (i < source.length) {
    const two = source.slice(i, i + 2)

    if (two === '//') {
      const end = source.indexOf('\n', i)
      const stop = end === -1 ? source.length : end
      out += keepNewlines(source.slice(i, stop))
      i = stop
      continue
    }

    if (two === '/*') {
      const end = source.indexOf('*/', i + 2)
      const stop = end === -1 ? source.length : end + 2
      out += keepNewlines(source.slice(i, stop))
      i = stop
      continue
    }

    const ch = source[i]
    if (ch === '"' || ch === "'" || ch === '`') {
      // 리터럴은 그대로 둔다. 탐지 대상이 바로 이것이다.
      let j = i + 1
      while (j < source.length) {
        if (source[j] === '\\') { j += 2; continue }
        if (source[j] === ch) { j += 1; break }
        // 따옴표 문자열은 줄을 넘지 않는다. 안 끊으면 뒤가 통째로 먹힌다.
        if (source[j] === '\n' && ch !== '`') break
        j += 1
      }
      out += source.slice(i, j)
      i = j
      continue
    }

    out += ch
    i += 1
  }
  return out
}

function checkHardcoded() {
  const files = SOURCE_ROOTS.flatMap((r) => walk(r))
  if (files.length === 0) {
    warn('프론트 소스가 없어 하드코딩 검사를 건너뛴다')
    return
  }
  for (const file of files) {
    const rel = relative(ROOT, file)
    // 카탈로그 자체와 i18n 설정은 문자열을 직접 다룬다.
    if (rel.includes('/i18n/') || rel.endsWith('i18n.ts')) continue

    // 예외 표시는 주석에 단다. 주석은 아래에서 지워지므로 원문 줄에서 본다 —
    // 지워진 줄에서 찾으면 표시가 영영 안 걸려 예외를 못 만든다.
    const source = readFileSync(file, 'utf8')
    const raw = source.split('\n')

    stripComments(source).split('\n').forEach((line, i) => {
      const trimmed = line.trim()
      if (!trimmed) return
      if (TRANSLATED.test(line) || ALLOWED_ATTRS.test(line)) return
      if (/\bi18n-exempt\b/.test(raw[i] ?? '')) return

      for (const match of line.matchAll(/(['"`])((?:(?!\1)[^\\]|\\.)*)\1/g)) {
        const literal = match[2]
        if (literal.length < 4) continue
        if (SENTENCE.test(literal)) {
          fail(`${rel}:${i + 1} 번역을 거치지 않은 문장 리터럴: ${JSON.stringify(literal)}`)
          break
        }
      }
    })
  }
}

// ── 5. 미사용 키 (경고) ──────────────────────────────────────────

/**
 * `t(`errors:${code}`)` 처럼 조립해 쓰는 키를 알아본다.
 *
 * 정적 문자열만 찾으면 이런 키가 전부 미사용으로 잡힌다. 한때 271개가
 * 떴는데, 그만큼 뜨는 경고는 아무도 읽지 않는다 — 경고가 곧 죽은 키를
 * 뜻해야 쓸모가 있다.
 */
function dynamicKeyMatchers(sources) {
  const out = []
  for (const source of sources) {
    for (const [, template] of source.matchAll(/`([^`\\]*\$\{[^`]*)`/g)) {
      // i18n 키는 리터럴 네임스페이스로 시작한다(`errors:${code}`). 이걸
      // 요구하지 않으면 `${a}:${b}` 같은 React key 하나가 모든 키를
      // "쓰이는 중" 으로 만들어 경고를 통째로 없앤다(실제로 그랬다).
      if (!/^[A-Za-z][A-Za-z0-9_-]*:/.test(template)) continue
      const literals = template.split(/\$\{[^}]*\}/g)
      const pattern = literals
        .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('[A-Za-z0-9_.-]+')
      out.push(new RegExp(`^${pattern}$`))
    }
  }
  return out
}

/**
 * **화면이 부르는 키가 카탈로그에 있는가.**
 *
 * 지금까지 이 검사만 없었다. 카탈로그끼리(en↔ko)는 맞춰 보고, 소스에서
 * 번역을 안 거친 문장을 찾고, 카탈로그에만 있고 안 쓰이는 키를 알려 주는데,
 * **없는 키를 부르는 것**은 아무도 안 봤다. 그 결과가 어떻게 보이는지:
 * `t('common:action.edit')` 이라고 적었고 그 키는 없었고, 화면의 버튼에
 * `action.edit` 이 글자 그대로 찍혔다. 게이트는 통과했다. 브라우저로 몰아
 * 보다가 접근성 트리에서 발견했다.
 *
 * `checkUnused` 가 이미 소스 전체를 훑으므로 방향만 뒤집으면 된다.
 *
 * 경고가 아니라 **실패**다. 없는 키는 사용자가 날것으로 보는 문자열이고,
 * 그건 카탈로그가 덜 채워진 것과 다른 종류의 결함이다.
 */
function checkMissing(catalogs) {
  const known = catalogs.get(SOURCE_LOCALE)
  const files = USAGE_ROOTS.flatMap((r) => walk(r, [], USAGE_EXTENSIONS))
  if (files.length === 0) {
    fail('키 사용처를 하나도 못 찾았다 — USAGE_ROOTS 경로가 틀렸다')
    return
  }

  // `t('ns:key')` 형태의 **리터럴만** 본다. 조립해 쓰는 키(`ns:a.${x}`)는
  // 무엇이 올지 정적으로 알 수 없으므로 여기서 판단하지 않는다 — 추측으로
  // 실패시키면 게이트를 끄게 되고, 그러면 리터럴도 같이 안 보게 된다.
  const CALL = /\bt\(\s*(['"`])([A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9_.-]+)\1/g
  const missing = new Map()
  for (const file of files) {
    const text = readFileSync(file, 'utf8')
    for (const [, , full] of text.matchAll(CALL)) {
      if (known.has(full)) continue
      missing.set(full, [...(missing.get(full) ?? []), relative(ROOT, file)])
    }
  }
  if (missing.size === 0) return
  const lines = [...missing]
    .map(([full, where]) => `      ${full} — ${[...new Set(where)].join(', ')}`)
    .join('\n')
  fail(
    `화면이 부르는데 카탈로그에 없는 키 ${missing.size}개 (사용자가 키를 날것으로 본다)\n${lines}`,
  )
}

function checkUnused(catalogs) {
  const files = USAGE_ROOTS.flatMap((r) => walk(r, [], USAGE_EXTENSIONS))
  if (files.length === 0) return
  const sources = files.map((f) => readFileSync(f, 'utf8'))
  const haystack = sources.join('\n')
  const matchers = dynamicKeyMatchers(sources)

  const unused = [...catalogs.get(SOURCE_LOCALE).keys()].filter((full) => {
    const [, key] = full.split(':')
    if (haystack.includes(key)) return false
    return !matchers.some((re) => re.test(full))
  })
  if (unused.length === 0) return

  // 네임스페이스로 묶어 보여 준다. 한 줄에 80개를 늘어놓으면 아무도 안 읽고,
  // 안 읽히는 경고는 없는 것과 같다.
  const byNamespace = new Map()
  for (const full of unused) {
    const [ns, key] = full.split(':')
    byNamespace.set(ns, [...(byNamespace.get(ns) ?? []), key])
  }
  const lines = [...byNamespace]
    .map(([ns, keys]) => `      ${ns} (${keys.length}): ${keys.join(', ')}`)
    .join('\n')
  warn(`아직 화면이 안 쓰는 키 ${unused.length}개 — 기능보다 카탈로그가 먼저 온 것들이다\n${lines}`)
}

// ── 실행 ─────────────────────────────────────────────────────────
const catalogs = new Map(locales().map((l) => [l, loadCatalog(l)]))
console.log(`i18n 검사: ${[...catalogs.keys()].join(', ')} / 키 ${catalogs.get(SOURCE_LOCALE)?.size ?? 0}개`)

checkKeySync(catalogs)
checkIcu(catalogs)
checkHardcoded()
checkMissing(catalogs)
checkUnused(catalogs)

for (const w of warnings) console.warn(`  경고  ${w}`)
if (problems.length) {
  console.error(`\n실패 ${problems.length}건:`)
  for (const p of problems) console.error(`  - ${p}`)
  process.exit(1)
}
console.log('  통과: 키 동기화 · ICU 문법 · 플레이스홀더 일치 · 하드코딩 없음 · 없는 키 호출 없음')
