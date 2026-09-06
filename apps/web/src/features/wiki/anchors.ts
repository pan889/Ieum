/**
 * 화면에서 인용 앵커를 만들고 찾는다 (wiki-markdown.md 6절).
 *
 * 서버는 문서의 **평문**에 대고 같은 인용을 찾는다. 여기서는 그 평문에
 * 해당하는 것이 렌더된 DOM 의 텍스트다. 사람은 렌더된 글을 드래그하지
 * `**굵게**` 같은 원문을 고르지 않으므로 두 좌표계가 맞아떨어진다.
 *
 * 오프셋은 주고받지 않는다 — 서버가 인용문으로 다시 찾는다. 그래서 표 셀
 * 사이 공백처럼 두 쪽 평문이 미세하게 다른 자리가 있어도 앵커는 살아남는다.
 */

import type { CommentAnchor } from '@ieum/api-client'

/** 앞뒤 문맥 길이. 서버의 CONTEXT_SIZE 와 맞춘다. */
export const CONTEXT_SIZE = 40
/** 인용문 상한. 문단을 통째로 인용하면 앵커가 아니라 복사본이다. */
export const MAX_QUOTE = 1000

export function normalizeText(text: string): string {
  return collapse(text).trim()
}

/**
 * 공백만 하나로 접는다. **다듬지 않는다.**
 *
 * 앞뒤 문맥에서는 붙어 있던 공백이 단서다. `"배포 전 "` 의 끝 공백을 떼면
 * 인용 바로 앞 글자와 어긋나 문맥 비교가 헛돈다.
 */
export function collapse(text: string): string {
  return text.replace(/\s+/g, ' ')
}

/** 블록이 바뀌면 사이에 공백이 하나 있는 것으로 친다. 서버 평문과 같다. */
const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT', 'FIGCAPTION',
  'FIGURE', 'FOOTER', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI', 'MAIN',
  'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'TD', 'TH', 'TR', 'UL',
])

function blockOf(node: Node, container: HTMLElement): Node {
  let current: Node | null = node.parentNode
  while (current && current !== container) {
    if (current instanceof HTMLElement && BLOCK_TAGS.has(current.tagName)) return current
    current = current.parentNode
  }
  return container
}

export interface DraftAnchor {
  exact: string
  prefix: string
  suffix: string
  occurrence: number
}

/**
 * 지금 선택된 구간에서 앵커를 만든다. 선택이 없거나 범위 밖이면 null.
 *
 * 오프셋은 DOM 이 알려 주는 대로 세지 않는다. 마크다운 렌더 결과는 태그가
 * 섞여 있어 `textContent` 길이와 화면 글자 수가 다르다. 대신 **선택 앞쪽
 * 텍스트를 통째로 꺼내 정규화한 길이**를 쓴다 — 서버가 보는 평문과 같은
 * 방식이다.
 */
export function anchorFromSelection(
  container: HTMLElement,
  selection: Selection | null,
): DraftAnchor | null {
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) return null

  const exact = normalizeText(range.toString()).slice(0, MAX_QUOTE)
  if (!exact) return null

  const before = range.cloneRange()
  before.selectNodeContents(container)
  before.setEnd(range.startContainer, range.startOffset)
  const head = normalizeText(before.toString())

  const after = range.cloneRange()
  after.selectNodeContents(container)
  after.setStart(range.endContainer, range.endOffset)

  return {
    exact,
    // 문맥은 다듬지 않는다 — 인용에 붙어 있던 공백이 단서다.
    prefix: collapse(before.toString()).slice(-CONTEXT_SIZE),
    suffix: collapse(after.toString()).slice(0, CONTEXT_SIZE),
    occurrence: countOccurrences(head, exact) + 1,
  }
}

export function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0
  let count = 0
  let at = haystack.indexOf(needle)
  while (at !== -1) {
    count += 1
    at = haystack.indexOf(needle, at + 1)
  }
  return count
}

/** 인용이 걸친 텍스트 노드 한 조각. */
export interface QuoteSegment {
  node: Text
  start: number
  end: number
}

/**
 * 인용이 지금 화면 어디에 있는지. 없으면 null.
 *
 * **텍스트 노드 조각들로** 돌려준다. 하나의 Range 로 감싸면
 * `surroundContents` 가 태그 경계를 걸친 범위에서 던진다 — `**굵게**` 뒤로
 * 조금 이어지는 인용이 딱 그 경우고, 그때 하이라이트가 통째로 사라졌다.
 *
 * 서버가 이미 고아 여부를 판정해서 준다 — 여기서 찾는 것은 **하이라이트를
 * 그릴 자리**뿐이다. 못 찾아도 코멘트는 목록에 그대로 남는다.
 */
export function findQuoteSegments(
  container: HTMLElement,
  anchor: CommentAnchor,
): QuoteSegment[] | null {
  const quote = normalizeText(anchor.exact)
  if (!quote) return null

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  // 텍스트 노드를 이어 붙이면서 정규화된 오프셋 → (노드, 노드 안 위치) 표를 만든다.
  const nodes: Text[] = []
  const offsets: number[] = []
  let text = ''
  let previousBlock: Node | null = null
  let node = walker.nextNode()
  while (node) {
    const block = blockOf(node, container)
    // 문단이 바뀌면 사이에 공백이 하나 있는 것으로 친다. 안 넣으면
    // `<p>앞</p><p>뒤</p>` 가 "앞뒤" 가 되어 서버 평문과 어긋난다.
    if (previousBlock !== null && block !== previousBlock && text !== '' && !text.endsWith(' ')) {
      text += ' '
      nodes.push(node as Text)
      offsets.push(0)
    }
    previousBlock = block

    const value = node.nodeValue ?? ''
    for (let i = 0; i < value.length; i += 1) {
      const char = /\s/.test(value[i] as string) ? ' ' : (value[i] as string)
      // 공백은 하나로 접는다. 서버 평문과 같은 규칙이어야 한다.
      if (char === ' ' && (text === '' || text.endsWith(' '))) continue
      text += char
      nodes.push(node as Text)
      offsets.push(i)
    }
    node = walker.nextNode()
  }
  text = text.trimEnd()

  const from = nth(text, quote, pickOccurrence(text, quote, anchor))
  if (from === -1) return null
  const to = from + quote.length - 1
  if (!nodes[from] || !nodes[to]) return null

  const segments: QuoteSegment[] = []
  for (let i = from; i <= to; i += 1) {
    const node = nodes[i] as Text
    const offset = offsets[i] as number
    const last = segments[segments.length - 1]
    if (last && last.node === node && last.end === offset) {
      last.end = offset + 1
    } else {
      segments.push({ node, start: offset, end: offset + 1 })
    }
  }
  return segments
}

/** 조각들을 하나의 Range 로. 읽기 전용으로만 쓴다(감싸기에는 못 쓴다). */
export function findQuote(container: HTMLElement, anchor: CommentAnchor): Range | null {
  const segments = findQuoteSegments(container, anchor)
  if (!segments || segments.length === 0) return null
  const first = segments[0] as QuoteSegment
  const last = segments[segments.length - 1] as QuoteSegment
  const range = document.createRange()
  range.setStart(first.node, first.start)
  range.setEnd(last.node, last.end)
  return range
}

/** 문맥이 있으면 그걸로, 없으면 순번으로 고른다. 서버와 같은 순서다. */
function pickOccurrence(text: string, quote: string, anchor: CommentAnchor): number {
  const prefix = collapse(anchor.prefix)
  if (!prefix) return anchor.occurrence
  let best = 1
  let bestScore = -1
  let seen = 0
  let at = text.indexOf(quote)
  while (at !== -1) {
    seen += 1
    const before = text.slice(Math.max(0, at - prefix.length), at)
    const score = before.endsWith(prefix) ? prefix.length : commonTail(before, prefix)
    if (score > bestScore) {
      bestScore = score
      best = seen
    }
    at = text.indexOf(quote, at + 1)
  }
  return best
}

function commonTail(a: string, b: string): number {
  let n = 0
  while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n += 1
  return n
}

function nth(haystack: string, needle: string, occurrence: number): number {
  let at = haystack.indexOf(needle)
  for (let seen = 1; at !== -1 && seen < occurrence; seen += 1) {
    at = haystack.indexOf(needle, at + 1)
  }
  return at
}
