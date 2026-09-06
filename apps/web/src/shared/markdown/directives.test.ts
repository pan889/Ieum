import { describe, expect, it } from 'vitest'

import { renderMarkdown } from './dialect'
import { collectHeadings, headingSlug, parseAttrs, splitDirectives } from './directives'

describe('디렉티브 인자', () => {
  it('맨값과 따옴표값을 읽는다', () => {
    expect(parseAttrs('depth=3 query="a b c"')).toEqual({ depth: '3', query: 'a b c' })
  })

  it('IQL 을 통째로 담는다', () => {
    // 공백에서 끊기면 질의를 쓸 수 없다.
    expect(parseAttrs('query="project = ENG AND status != Done"')['query']).toBe(
      'project = ENG AND status != Done',
    )
  })

  it('키는 소문자로 맞춘다', () => {
    expect(parseAttrs('Depth=2')).toEqual({ depth: '2' })
  })
})

describe('본문 나누기', () => {
  it('리프 디렉티브를 떼어낸다', () => {
    expect(splitDirectives('앞\n\n::toc{depth=2}\n\n뒤')).toEqual([
      { kind: 'markdown', text: '앞\n' },
      { kind: 'directive', name: 'toc', attrs: { depth: '2' }, body: null },
      { kind: 'markdown', text: '\n뒤' },
    ])
  })

  it('컨테이너는 본문을 담는다', () => {
    expect(splitDirectives(':::info\n상자\n:::')).toEqual([
      { kind: 'directive', name: 'info', attrs: {}, body: '상자' },
    ])
  })

  it('모르는 이름은 그냥 텍스트다', () => {
    // 남의 도구가 만든 문서에서 `::` 로 시작하는 줄 하나 때문에 화면이
    // 비면 안 된다. 미지원 뷰어에서 텍스트로 읽히는 것과 같은 성질이다.
    const segments = splitDirectives('::excerpt{page="ENG/x"}')
    expect(segments).toEqual([{ kind: 'markdown', text: '::excerpt{page="ENG/x"}' }])
  })

  it('코드 펜스 안은 예제다', () => {
    const source = '```\n::toc\n:::info\nx\n:::\n```'
    expect(splitDirectives(source)).toEqual([{ kind: 'markdown', text: source }])
  })

  it('닫히지 않은 컨테이너는 문서를 삼키지 않는다', () => {
    const source = ':::info\n닫는 줄이 없다\n\n계속되는 본문'
    expect(splitDirectives(source)).toEqual([{ kind: 'markdown', text: source }])
  })

  it('중첩은 더 긴 울타리로 감싼다', () => {
    const segments = splitDirectives('::::info\n:::note\n안\n:::\n::::')
    expect(segments).toEqual([
      { kind: 'directive', name: 'info', attrs: {}, body: ':::note\n안\n:::' },
    ])
  })

  it('들여쓴 줄은 디렉티브가 아니다', () => {
    expect(splitDirectives('  ::toc')).toEqual([{ kind: 'markdown', text: '  ::toc' }])
  })
})

describe('제목 모으기', () => {
  it('원문에서 뽑는다', () => {
    expect(collectHeadings('# 하나\n\n## 둘')).toEqual([
      { level: 1, text: '하나', slug: '하나' },
      { level: 2, text: '둘', slug: '둘' },
    ])
  })

  it('코드 안의 # 은 제목이 아니다', () => {
    expect(collectHeadings('# 진짜\n\n```\n# 가짜\n```')).toHaveLength(1)
  })

  it('depth 를 넘는 제목은 뺀다', () => {
    expect(collectHeadings('# 하나\n\n### 셋', 2)).toEqual([
      { level: 1, text: '하나', slug: '하나' },
    ])
  })

  it('같은 제목에는 번호를 붙인다', () => {
    expect(collectHeadings('## 같음\n\n## 같음').map((h) => h.slug)).toEqual(['같음', '같음-2'])
  })

  it('한글을 로마자로 옮기지 않는다', () => {
    // 옮기면 원래 제목을 되짚을 수 없다 (slug.py 와 같은 규칙).
    expect(headingSlug('배포 절차')).toBe('배포-절차')
  })
})

describe('렌더', () => {
  it('제목에 목차가 가리킬 id 를 붙인다', () => {
    expect(renderMarkdown('# 배포 절차')).toContain('id="배포-절차"')
  })

  it('중복 번호는 넘겨준 맵으로 센다', () => {
    // 문서가 조각으로 나뉘어 그려져도 목차 링크가 맞아야 한다.
    const slugs = new Map<string, number>()
    expect(renderMarkdown('## 같음', slugs)).toContain('id="같음"')
    expect(renderMarkdown('## 같음', slugs)).toContain('id="같음-2"')
  })

  it('떼어내지 않은 컨테이너도 상자로 그린다', () => {
    // 목록 안처럼 나누지 않는 자리에도 컨테이너가 올 수 있다.
    const html = renderMarkdown('- 항목\n\n  :::info\n  안내\n  :::\n')
    expect(html).toContain('ieum-admonition')
  })

  it('상자 이름으로 태그를 빠져나갈 수 없다', () => {
    const html = renderMarkdown(':::"><script>alert(1)</script>\nx\n:::')
    expect(html).not.toContain('<script>')
  })
})
