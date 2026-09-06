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

function walk(dir, acc = []) {
  if (!existsSync(dir)) return acc
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, acc)
    else if (['.ts', '.tsx'].includes(extname(entry)) && !entry.endsWith('.d.ts')) acc.push(full)
  }
  return acc
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

    readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('//') || trimmed.startsWith('*')) return
      if (TRANSLATED.test(line) || ALLOWED_ATTRS.test(line)) return
      if (/\bi18n-exempt\b/.test(line)) return

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
function checkUnused(catalogs) {
  const files = SOURCE_ROOTS.flatMap((r) => walk(r))
  if (files.length === 0) return
  const haystack = files.map((f) => readFileSync(f, 'utf8')).join('\n')
  const unused = [...catalogs.get(SOURCE_LOCALE).keys()].filter((full) => {
    const [, key] = full.split(':')
    return !haystack.includes(key)
  })
  if (unused.length) {
    warn(`미사용으로 보이는 키 ${unused.length}개 (실패 아님): ${unused.slice(0, 8).join(', ')}${unused.length > 8 ? ' …' : ''}`)
  }
}

// ── 실행 ─────────────────────────────────────────────────────────
const catalogs = new Map(locales().map((l) => [l, loadCatalog(l)]))
console.log(`i18n 검사: ${[...catalogs.keys()].join(', ')} / 키 ${catalogs.get(SOURCE_LOCALE)?.size ?? 0}개`)

checkKeySync(catalogs)
checkIcu(catalogs)
checkHardcoded()
checkUnused(catalogs)

for (const w of warnings) console.warn(`  경고  ${w}`)
if (problems.length) {
  console.error(`\n실패 ${problems.length}건:`)
  for (const p of problems) console.error(`  - ${p}`)
  process.exit(1)
}
console.log('  통과: 키 동기화 · ICU 문법 · 플레이스홀더 일치 · 하드코딩 없음')
