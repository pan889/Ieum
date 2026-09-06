import { describe, expect, it } from 'vitest'

import type { CommentAnchor } from '@ieum/api-client'

import {
  anchorFromSelection,
  countOccurrences,
  findQuote,
  findQuoteSegments,
  normalizeText,
} from './anchors'

function render(html: string): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = html
  document.body.replaceChildren(el)
  return el
}

function anchor(partial: Partial<CommentAnchor>): CommentAnchor {
  return { exact: '', prefix: '', suffix: '', occurrence: 1, version_number: null, ...partial }
}

/** 텍스트 노드 안의 오프셋으로 선택 범위를 만든다. */
function select(container: HTMLElement, text: string): Selection {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    const at = (node.nodeValue ?? '').indexOf(text)
    if (at !== -1) {
      const range = document.createRange()
      range.setStart(node, at)
      range.setEnd(node, at + text.length)
      const selection = window.getSelection() as Selection
      selection.removeAllRanges()
      selection.addRange(range)
      return selection
    }
    node = walker.nextNode()
  }
  throw new Error(`선택할 텍스트를 못 찾았다: ${text}`)
}

describe('normalizeText', () => {
  it('공백을 하나로 접는다', () => {
    // 서버 평문과 같은 규칙이어야 인용이 맞는다.
    expect(normalizeText('  가  나\n\n다  ')).toBe('가 나 다')
  })
})

describe('countOccurrences', () => {
  it('겹치지 않게 센다', () => {
    expect(countOccurrences('가나 가나 가나', '가나')).toBe(3)
  })

  it('빈 문자열은 0', () => {
    expect(countOccurrences('가나', '')).toBe(0)
  })
})

describe('anchorFromSelection', () => {
  it('선택한 글과 앞뒤 문맥을 담는다', () => {
    const el = render('<p>배포 전 마이그레이션을 검토한다. 그리고 롤백을 준비한다.</p>')
    const draft = anchorFromSelection(el, select(el, '마이그레이션을 검토한다'))
    expect(draft).not.toBeNull()
    expect(draft?.exact).toBe('마이그레이션을 검토한다')
    expect(draft?.prefix).toBe('배포 전 ')
    expect(draft?.suffix.startsWith('.')).toBe(true)
  })

  it('같은 글이 앞에 있으면 순번을 올린다', () => {
    const el = render('<p>확인한다. 사이 문장. 확인한다.</p>')
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    const node = walker.nextNode() as Text
    const range = document.createRange()
    const at = (node.nodeValue ?? '').lastIndexOf('확인한다')
    range.setStart(node, at)
    range.setEnd(node, at + 4)
    const selection = window.getSelection() as Selection
    selection.removeAllRanges()
    selection.addRange(range)

    expect(anchorFromSelection(el, selection)?.occurrence).toBe(2)
  })

  it('선택이 없으면 null', () => {
    const el = render('<p>본문</p>')
    window.getSelection()?.removeAllRanges()
    expect(anchorFromSelection(el, window.getSelection())).toBeNull()
  })

  it('바깥을 선택하면 null', () => {
    const el = render('<p>안</p>')
    const outside = document.createElement('p')
    outside.textContent = '밖'
    document.body.append(outside)
    const range = document.createRange()
    range.selectNodeContents(outside)
    const selection = window.getSelection() as Selection
    selection.removeAllRanges()
    selection.addRange(range)

    expect(anchorFromSelection(el, selection)).toBeNull()
  })
})

describe('findQuote', () => {
  it('태그를 넘어가는 인용도 찾는다', () => {
    // 렌더된 마크다운은 태그가 섞여 있다. 텍스트 노드 하나로 가정하면 안 된다.
    const el = render('<p>배포 전 <strong>마이그레이션</strong>을 검토한다.</p>')
    const range = findQuote(el, anchor({ exact: '마이그레이션을 검토한다' }))
    expect(range).not.toBeNull()
    expect(normalizeText(range?.toString() ?? '')).toBe('마이그레이션을 검토한다')
  })

  it('블록이 갈려 있어도 공백 하나로 이어 본다', () => {
    const el = render('<p>앞 문단</p><p>뒤 문단</p>')
    expect(findQuote(el, anchor({ exact: '앞 문단 뒤 문단' }))).not.toBeNull()
  })

  it('문맥으로 몇 번째인지 고른다', () => {
    const el = render('<p>확인한다. 사이. 확인한다.</p>')
    const range = findQuote(el, anchor({ exact: '확인한다', prefix: '사이. ' }))
    expect(range).not.toBeNull()
    expect(range?.startOffset).toBeGreaterThan(5)
  })

  it('순번으로도 고를 수 있다', () => {
    const el = render('<p>확인한다. 사이. 확인한다.</p>')
    const first = findQuote(el, anchor({ exact: '확인한다', occurrence: 1 }))
    const second = findQuote(el, anchor({ exact: '확인한다', occurrence: 2 }))
    expect(second?.startOffset).toBeGreaterThan(first?.startOffset ?? 0)
  })

  it('없으면 null — 코멘트는 목록에 그대로 남는다', () => {
    const el = render('<p>완전히 다른 내용</p>')
    expect(findQuote(el, anchor({ exact: '마이그레이션' }))).toBeNull()
  })

  it('빈 인용은 아무 데도 안 붙는다', () => {
    const el = render('<p>본문</p>')
    expect(findQuote(el, anchor({ exact: '   ' }))).toBeNull()
  })
})

describe('findQuoteSegments', () => {
  it('태그 경계를 넘으면 조각으로 나눠 준다', () => {
    // 범위 하나로 감싸면 `surroundContents` 가 던진다 — 실제로 하이라이트가
    // 통째로 사라졌다. 조각마다 감싸야 한다.
    const el = render('<p><strong>마이그레이션을 꼭 검토한다</strong>. 뒤 문장.</p>')
    const segments = findQuoteSegments(el, anchor({ exact: '마이그레이션을 꼭 검토한다.' }))
    expect(segments).not.toBeNull()
    expect((segments ?? []).length).toBeGreaterThan(1)
    // 조각을 이으면 인용이 된다.
    const joined = (segments ?? [])
      .map((s) => (s.node.nodeValue ?? '').slice(s.start, s.end))
      .join('')
    expect(normalizeText(joined)).toBe('마이그레이션을 꼭 검토한다.')
  })

  it('조각마다 감싸도 던지지 않는다', () => {
    const el = render('<p><strong>굵은 부분</strong>과 뒤</p>')
    const segments = findQuoteSegments(el, anchor({ exact: '굵은 부분과 뒤' })) ?? []
    for (const segment of [...segments].reverse()) {
      const range = document.createRange()
      range.setStart(segment.node, segment.start)
      range.setEnd(segment.node, segment.end)
      expect(() => { range.surroundContents(document.createElement('mark')) }).not.toThrow()
    }
    expect(el.querySelectorAll('mark').length).toBe(segments.length)
  })
})
