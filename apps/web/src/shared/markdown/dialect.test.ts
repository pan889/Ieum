import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { LINK_SCHEMES, OPTIONS, asSpec, renderMarkdown, validateLink } from './dialect'

// vitest 의 cwd 는 apps/web 이다. jsdom 환경에서 import.meta.url 은 http URL
// 이라 fileURLToPath 가 안 통한다.
const SPEC_PATH = resolve(process.cwd(), '../../packages/markdown/dialect.json')

interface Spec {
  $comment?: string
  version: number
  preset: string
  options: Record<string, boolean>
  core: string[]
  plugins: string[]
  linkSchemes: string[]
}

describe('방언 계약', () => {
  it('명세 파일과 일치한다', () => {
    // 서버(core/markdown/dialect.py)도 같은 파일과 비교한다. 한쪽만 고치면
    // 둘 중 하나가 반드시 실패한다.
    const spec = JSON.parse(readFileSync(SPEC_PATH, 'utf8')) as Spec
    delete spec.$comment
    expect(asSpec()).toEqual(spec)
  })

  it('원시 HTML 은 꺼져 있다', () => {
    expect(OPTIONS.html).toBe(false)
  })
})

describe('새니타이징', () => {
  const hrefs = (html: string) => [...html.matchAll(/(?:href|src)="([^"]*)"/gi)].map((m) => m[1])

  it.each([
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    "<iframe src='https://evil.example'></iframe>",
    '<svg/onload=alert(1)>',
  ])('원시 HTML 을 그리지 않는다: %s', (source) => {
    const html = renderMarkdown(source)
    expect(html.toLowerCase()).not.toContain('<script')
    expect(html.toLowerCase()).not.toContain('<iframe')
    expect(html.toLowerCase()).not.toContain('<img src=x')
  })

  it.each([
    '[x](javascript:alert(1))',
    '[x](JaVaScRiPt:alert(1))',
    '[x](data:text/html;base64,PHNjcmlwdD4=)',
    '[x](vbscript:msgbox)',
    '![x](javascript:alert(1))',
  ])('위험한 스킴은 링크가 되지 않는다: %s', (source) => {
    expect(hrefs(renderMarkdown(source))).toEqual([])
  })

  // user: 는 링크가 아니라 멘션 칩으로 그린다 (아래 '멘션' 블록 참고).
  it.each(LINK_SCHEMES.filter((s) => s !== 'user'))('%s 스킴은 링크가 된다', (scheme) => {
    const url = scheme === 'mailto' ? 'mailto:a@b.com' : `${scheme}://x/y`
    expect(hrefs(renderMarkdown(`[x](${url})`))).toEqual([url])
  })

  it('화이트리스트가 정책이다', () => {
    expect(validateLink('javascript:alert(1)')).toBe(false)
    expect(validateLink('ftp://example.com')).toBe(false)
    expect(validateLink('issue:IEUM-1')).toBe(true)
    expect(validateLink('#anchor')).toBe(true)
  })
})

describe('렌더', () => {
  it('GFM 표·체크박스·취소선을 그린다', () => {
    expect(renderMarkdown('| a |\n| - |\n| 1 |\n')).toContain('<table>')
    expect(renderMarkdown('- [x] done\n')).toContain('type="checkbox"')
    expect(renderMarkdown('~~x~~')).toContain('<s>')
  })

  it('front matter 는 본문에 안 나온다', () => {
    const html = renderMarkdown('---\ntitle: secret\n---\n\nbody\n')
    expect(html).not.toContain('secret')
    expect(html).toContain('body')
  })

  it('닫히지 않은 --- 는 front matter 가 아니다', () => {
    // 문서 전체를 삼키면 본문이 통째로 사라진다.
    expect(renderMarkdown('---\ntitle: x\n\nbody\n')).toContain('body')
  })

  it('문서 중간의 --- 는 구분선이다', () => {
    const html = renderMarkdown('intro\n\n---\n\nrest\n')
    expect(html).toContain('<hr />')
    expect(html).toContain('intro')
    expect(html).toContain('rest')
  })
})

describe('멘션', () => {
  it('user: 링크는 칩이 된다', () => {
    const html = renderMarkdown('hi [@Alice](user:01a07619-e13a-751c-96f4-079a061f393f)')
    expect(html).toContain('<span class="ieum-mention">@Alice</span>')
    // 링크로 두면 눌렀을 때 아무 데도 못 간다.
    expect(html).not.toContain('href="user:')
  })

  it('일반 링크는 그대로 링크다', () => {
    const html = renderMarkdown('[a](https://x.example) [@B](user:01a07619-e13a-751c-96f4-079a061f393e)')
    expect(html).toContain('href="https://x.example"')
    expect(html).toContain('ieum-mention')
  })

  it('멘션이 여러 개여도 태그가 안 엉킨다', () => {
    const html = renderMarkdown(
      '[@A](user:01a07619-e13a-751c-96f4-079a061f393f) and [@B](user:01a07619-e13a-751c-96f4-079a061f393e)',
    )
    expect(html.match(/ieum-mention/g)).toHaveLength(2)
    expect(html.match(/<\/span>/g)).toHaveLength(2)
  })
})
