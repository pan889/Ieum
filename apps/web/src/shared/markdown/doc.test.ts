/**
 * WYSIWYG 라운드트립 계약.
 *
 * 편집기를 거쳤다는 이유로 문서가 달라지면 안 된다 (M2 완료 조건,
 * wiki-markdown.md 8절). 서버의 `normalize()` 를 여기서 부를 수 없으므로
 * **렌더 결과가 같은가**로 "의미상 동일" 을 본다 — 목록 마커나 표 여백이
 * 달라도 같은 HTML 이면 같은 문서다.
 *
 * 코퍼스는 서버 테스트와 같은 파일을 읽는다. 한쪽에만 두면 다른 쪽이 못
 * 다루는 구조가 조용히 늘어난다.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { renderMarkdown } from './dialect'
import { cycle, toDoc, toMarkdown } from './doc'

const corpus = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../packages/markdown/corpus.json'), 'utf8'),
) as { documents: { name: string; source: string }[] }

const CASES = corpus.documents.map((d) => [d.name, d.source] as const)

describe('편집기를 거쳐도 문서는 그대로다', () => {
  it.each(CASES)('%s', (_name, source) => {
    expect(renderMarkdown(cycle(source))).toBe(renderMarkdown(source))
  })
})

describe('한 번 더 돌려도 그대로다', () => {
  // 멱등하지 않으면 문서를 열었다 닫을 때마다 diff 가 생긴다.
  it.each(CASES)('%s', (_name, source) => {
    const once = cycle(source)
    expect(cycle(once)).toBe(once)
  })
})

describe('모르는 것은 원문을 지킨다', () => {
  it('front matter 를 잃지 않는다', () => {
    const source = '---\ntitle: 문서\nlabels: [a, b]\n---\n\n본문.'
    expect(cycle(source)).toContain('title: 문서')
    expect(cycle(source)).toContain('labels: [a, b]')
  })

  it('디렉티브 컨테이너를 그대로 둔다', () => {
    const source = ':::warning\n조심할 것.\n:::'
    expect(cycle(source)).toBe(source)
  })

  it('디렉티브 한 줄도 그대로 둔다', () => {
    const source = '::toc{depth=2}'
    expect(cycle(source)).toBe(source)
  })

  it('따옴표 안에 공백이 있는 인자도 그대로 둔다', () => {
    // `::chart`·`::issues` 의 질의는 공백을 담는다. 왕복에서 따옴표가
    // 벗겨지면 저장할 때 인자가 셋으로 쪼개져 저장이 거절된다.
    const source = '::chart{query="project = OPS" group=status limit=5}'
    expect(cycle(source)).toBe(source)
  })

  it('각주 정의를 잃지 않는다', () => {
    const source = '본문[^1]\n\n[^1]: 각주 내용'
    expect(cycle(source)).toContain('[^1]: 각주 내용')
  })
})

describe('문서 구조', () => {
  it('제목은 수준을 지킨다', () => {
    const doc = toDoc('## 가운데')
    expect(doc.content?.[0]).toMatchObject({ type: 'heading', attrs: { level: 2 } })
  })

  it('체크박스는 taskItem 이 된다', () => {
    const doc = toDoc('- [x] 함\n- [ ] 안 함')
    const list = doc.content?.[0] as { type: string; content?: { attrs?: { checked?: boolean } }[] }
    expect(list.type).toBe('taskList')
    expect(list.content?.map((i) => i.attrs?.checked)).toEqual([true, false])
  })

  it('링크는 마크로 붙는다', () => {
    const doc = toDoc('[문서](page:ENG/deploy)')
    const paragraph = doc.content?.[0] as { content?: { marks?: { type: string; attrs?: { href: string } }[] }[] }
    expect(paragraph.content?.[0]?.marks?.[0]).toMatchObject({
      type: 'link',
      attrs: { href: 'page:ENG/deploy' },
    })
  })

  it('빈 문서에도 문단 하나는 있다', () => {
    // 완전히 빈 문서를 주면 편집기가 커서를 놓을 자리가 없다.
    expect(toDoc('').content).toEqual([{ type: 'paragraph' }])
    expect(toMarkdown(toDoc(''))).toBe('')
  })
})

describe('표 셀 안의 막대', () => {
  /*
    **셀이 갈라지면 그 줄이 통째로 어긋난다.**

    `a | b` 한 칸이 두 칸이 되면 그 뒤가 한 칸씩 밀리고, 헤더 폭을 넘어간
    값은 어느 파서에서든 버려진다. 편집기를 한 번 열었다 닫은 것만으로
    표 하나가 망가지고, 되돌릴 원본은 이미 없다.
  */
  it('편집기를 거쳐도 살아남는다', () => {
    const source = ['| 식 | 뜻 |', '| --- | --- |', '| a \\| b | 또는 |'].join('\n')
    const again = cycle(source)
    expect(again).toContain('a \\| b')
    // 렌더 결과가 같아야 "같은 문서" 다.
    expect(renderMarkdown(again)).toBe(renderMarkdown(source))
  })

  it('칸이 늘어나지 않는다', () => {
    const source = ['| 식 | 뜻 |', '| --- | --- |', '| a \\| b | 또는 |'].join('\n')
    const body = cycle(source)
      .split('\n')
      .filter((line) => line.startsWith('|') && !line.includes('---'))
    // 머리글과 본문 모두 칸이 둘이어야 한다. 이스케이프를 잃으면 셋이 된다.
    for (const line of body) {
      expect(line.split(/(?<!\\)\|/).length - 2).toBe(2)
    }
  })

  it('인라인 코드 안의 막대도 감싼다', () => {
    // GFM 은 표를 나누는 일을 인라인 코드보다 **먼저** 한다. 코드 안이라고
    // 안 감싸면 거기서도 셀이 갈라진다.
    const source = ['| 식 | 뜻 |', '| --- | --- |', '| `a \\| b` | 또는 |'].join('\n')
    expect(cycle(source)).toContain('\\|')
    expect(renderMarkdown(cycle(source))).toBe(renderMarkdown(source))
  })
})
