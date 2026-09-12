/**
 * 마크다운 ↔ 편집 문서(TipTap JSON).
 *
 * 정본은 마크다운이다(ADR-0008). WYSIWYG 은 **보여주는 방식**일 뿐이라,
 * 편집기를 거쳤다는 이유로 문서가 달라지면 안 된다 — M2 완료 조건이 그
 * 라운드트립 계약이다(wiki-markdown.md 8절).
 *
 * 그래서 두 가지를 지킨다:
 *
 * 1. **파서를 새로 만들지 않는다.** `dialect.ts` 의 markdown-it 을 그대로
 *    쓴다. 편집기용 파서를 따로 두면 방언이 셋이 되고, 셋은 반드시 갈라진다.
 * 2. **모르는 블록은 원문을 지킨다.** 모델링한 블록이 차지한 줄 범위를 모아
 *    두고, 남은 줄은 통째로 `verbatim` 으로 담는다. front matter·각주 정의처럼
 *    토큰이 없거나 위치가 없는 것들이 여기로 떨어진다. "빠뜨릴 수 있는 것" 이
 *    아니라 "안 건드린 것" 이 되므로, 새 문법이 생겨도 문서가 깨지지 않는다.
 */

import type Token from 'markdown-it/lib/token.mjs'

import { parser } from './dialect'

export type MarkType = 'bold' | 'italic' | 'strike' | 'code' | 'link'

export interface Mark {
  type: MarkType
  attrs?: { href: string; title?: string | null }
}

export interface TextNode {
  type: 'text'
  text: string
  marks?: Mark[]
}

export interface ElementNode {
  type: string
  attrs?: Record<string, unknown>
  content?: DocNode[]
}

export type DocNode = TextNode | ElementNode

/**
 * `ElementNode.type` 이 `string` 이라 `node.type === 'text'` 만으로는 좁혀지지
 * 않는다. 노드 종류는 계속 늘어나므로 유니온을 리터럴로 다 적을 수는 없다.
 */
export function isText(node: DocNode): node is TextNode {
  return node.type === 'text'
}

const MARK_BY_TAG: Record<string, MarkType> = { strong: 'bold', em: 'italic', s: 'strike' }

/** 표 정렬. markdown-it 은 `style="text-align:left"` 로 준다. */
function alignOf(token: Token): string | null {
  const style = token.attrGet('style') ?? ''
  const found = /text-align:\s*(left|center|right)/.exec(style)
  return found ? (found[1] as string) : null
}

// ── 마크다운 → 문서 ──────────────────────────────────────────────

interface Block {
  from: number
  to: number
  node: ElementNode
}

/** 마크다운을 편집 문서로. 모델링하지 않은 줄은 원문 그대로 남는다. */
export function toDoc(markdown: string): ElementNode {
  const lines = markdown.split('\n')
  const tokens = parser().parse(markdown, {})
  const blocks: Block[] = []

  // 최상위(깊이 0) 토큰만 본다. 안쪽까지 훑으면 각주 정의 안의 문단이
  // 최상위 블록으로 올라와, 정작 `[^1]:` 줄은 "이미 다뤘다" 고 표시된 채
  // 사라진다.
  let index = 0
  let depth = 0
  while (index < tokens.length) {
    const token = tokens[index] as Token
    if (depth === 0 && token.map && token.nesting >= 0) {
      const built = buildBlock(tokens, index)
      if (built) {
        blocks.push({ from: token.map[0], to: token.map[1], node: built.node })
        index = built.next
        continue
      }
    }
    depth += token.nesting
    index += 1
  }

  blocks.sort((a, b) => a.from - b.from)

  const content: DocNode[] = []
  let cursor = 0
  for (const block of blocks) {
    // 앞에 남은 줄이 있으면 원문 그대로 담는다.
    pushVerbatim(content, lines, cursor, block.from)
    content.push(block.node)
    cursor = Math.max(cursor, block.to)
  }
  pushVerbatim(content, lines, cursor, lines.length)

  return { type: 'doc', content: content.length > 0 ? content : [{ type: 'paragraph' }] }
}

function pushVerbatim(into: DocNode[], lines: string[], from: number, to: number): void {
  if (to <= from) return
  const source = lines.slice(from, to).join('\n').replace(/^\n+|\n+$/g, '')
  if (source.trim() === '') return
  into.push({ type: 'verbatim', attrs: { source } })
}

interface Built {
  node: ElementNode
  next: number
}

/** 여는 토큰 하나를 블록 노드로. 자식은 닫는 토큰까지 재귀로 읽는다. */
function buildBlock(tokens: Token[], start: number): Built | null {
  const token = tokens[start] as Token
  switch (token.type) {
    case 'paragraph_open': {
      const inline = tokens[start + 1]
      return {
        node: { type: 'paragraph', content: inline ? inlineNodes(inline) : [] },
        next: closeOf(tokens, start) + 1,
      }
    }
    case 'heading_open': {
      const inline = tokens[start + 1]
      return {
        node: {
          type: 'heading',
          attrs: { level: Number(token.tag.slice(1)) },
          content: inline ? inlineNodes(inline) : [],
        },
        next: closeOf(tokens, start) + 1,
      }
    }
    case 'bullet_list_open':
    case 'ordered_list_open':
      return listNode(tokens, start)
    case 'blockquote_open': {
      const end = closeOf(tokens, start)
      return {
        node: { type: 'blockquote', content: childBlocks(tokens, start + 1, end) },
        next: end + 1,
      }
    }
    case 'fence':
    case 'code_block':
      return {
        node: {
          type: 'codeBlock',
          attrs: { language: token.info.trim() || null },
          content: token.content ? [{ type: 'text', text: stripTrailingNewline(token.content) }] : [],
        },
        next: start + 1,
      }
    case 'hr':
      return { node: { type: 'horizontalRule' }, next: start + 1 }
    case 'table_open':
      return tableNode(tokens, start)
    case 'colon_fence':
      // 디렉티브 컨테이너는 편집기가 다루지 않는다. 원문을 지킨다.
      return {
        node: {
          type: 'verbatim',
          attrs: { source: `${token.markup}${token.info}\n${token.content}${token.markup}` },
        },
        next: start + 1,
      }
    default:
      return null
  }
}

function stripTrailingNewline(text: string): string {
  return text.endsWith('\n') ? text.slice(0, -1) : text
}

/** 여는 토큰과 짝이 맞는 닫는 토큰의 위치. */
function closeOf(tokens: Token[], start: number): number {
  let depth = 0
  for (let i = start; i < tokens.length; i += 1) {
    const token = tokens[i] as Token
    depth += token.nesting
    if (depth === 0 && i > start) return i
    if (depth === 0 && token.nesting === 0) return i
  }
  return tokens.length - 1
}

function childBlocks(tokens: Token[], from: number, to: number): DocNode[] {
  const out: DocNode[] = []
  let index = from
  while (index < to) {
    const built = buildBlock(tokens, index)
    if (built) {
      out.push(built.node)
      index = built.next
    } else {
      index += 1
    }
  }
  return out
}

/** 체크박스 목록인지. tasklists 플러그인이 `<input>` 을 앞에 끼워 넣는다. */
function checkboxOf(tokens: Token[], itemStart: number): boolean | null {
  const inline = tokens[itemStart + 2]
  const first = inline?.children?.[0]
  if (!first || first.type !== 'html_inline') return null
  if (!first.content.includes('task-list-item-checkbox')) return null
  return first.content.includes('checked=')
}

function listNode(tokens: Token[], start: number): Built {
  const open = tokens[start] as Token
  const end = closeOf(tokens, start)
  const ordered = open.type === 'ordered_list_open'
  const items: DocNode[] = []
  let tasks = false
  // 촘촘한 목록에서는 markdown-it 이 문단을 `hidden` 으로 표시한다. 이걸
  // 안 기억하면 항목 사이에 빈 줄을 넣게 되고, 촘촘하던 목록이 성글어진다
  // (`<li>하나</li>` 가 `<li><p>하나</p></li>` 로 바뀐다).
  let tight = true

  let index = start + 1
  while (index < end) {
    const token = tokens[index] as Token
    if (token.type !== 'list_item_open') {
      index += 1
      continue
    }
    const itemEnd = closeOf(tokens, index)
    const checked = checkboxOf(tokens, index)
    if (checked !== null) tasks = true
    for (let i = index; i < itemEnd; i += 1) {
      const inner = tokens[i] as Token
      if (inner.type === 'paragraph_open' && !inner.hidden) tight = false
    }
    const body = childBlocks(tokens, index + 1, itemEnd)
    // `[x]` 를 걷어내면 그 뒤의 공백 한 칸이 본문 앞에 남는다. 그대로 두면
    // 되돌릴 때마다 공백이 하나씩 늘어난다.
    if (checked !== null) trimLeadingSpace(body)
    items.push({
      type: checked === null ? 'listItem' : 'taskItem',
      ...(checked === null ? {} : { attrs: { checked } }),
      content: body,
    })
    index = itemEnd + 1
  }

  const startAttr = ordered ? Number(open.attrGet('start') ?? 1) : 1
  return {
    node: {
      type: tasks ? 'taskList' : ordered ? 'orderedList' : 'bulletList',
      attrs: { tight, ...(ordered && startAttr !== 1 ? { start: startAttr } : {}) },
      content: items,
    },
    next: end + 1,
  }
}

function tableNode(tokens: Token[], start: number): Built {
  const end = closeOf(tokens, start)
  const rows: DocNode[] = []
  let cells: DocNode[] | null = null
  let header = false

  for (let index = start + 1; index < end; index += 1) {
    const token = tokens[index] as Token
    switch (token.type) {
      case 'thead_open':
        header = true
        break
      case 'thead_close':
        header = false
        break
      case 'tr_open':
        cells = []
        break
      case 'tr_close':
        if (cells) rows.push({ type: 'tableRow', content: cells })
        cells = null
        break
      case 'th_open':
      case 'td_open': {
        const inline = tokens[index + 1]
        const align = alignOf(token)
        cells?.push({
          type: header ? 'tableHeader' : 'tableCell',
          ...(align ? { attrs: { align } } : {}),
          content: [{ type: 'paragraph', content: inline ? inlineNodes(inline) : [] }],
        })
        break
      }
      default:
        break
    }
  }
  return { node: { type: 'table', content: rows }, next: end + 1 }
}

/** 인라인 토큰을 텍스트 + 마크로. */
function inlineNodes(inline: Token): DocNode[] {
  const out: DocNode[] = []
  const marks: Mark[] = []

  for (const token of inline.children ?? []) {
    switch (token.type) {
      case 'text':
        if (token.content) out.push(withMarks({ type: 'text', text: token.content }, marks))
        break
      case 'code_inline':
        out.push(withMarks({ type: 'text', text: token.content }, [...marks, { type: 'code' }]))
        break
      case 'softbreak':
        out.push(withMarks({ type: 'text', text: '\n' }, marks))
        break
      case 'hardbreak':
        out.push({ type: 'hardBreak' })
        break
      case 'image':
        out.push({
          type: 'image',
          attrs: {
            src: token.attrGet('src') ?? '',
            alt: token.children?.map((c) => c.content).join('') ?? '',
            title: token.attrGet('title'),
          },
        })
        break
      case 'link_open':
        marks.push({
          type: 'link',
          attrs: { href: token.attrGet('href') ?? '', title: token.attrGet('title') },
        })
        break
      case 'link_close':
        dropMark(marks, 'link')
        break
      case 'footnote_ref': {
        // 정의는 원문 그대로 남으므로 참조도 그대로 남겨야 짝이 맞는다.
        // 텍스트로 두면 `[` 이스케이프에 걸려 `\[^1\]` 이 된다.
        const label = (token.meta as { label?: string } | undefined)?.label
        out.push({ type: 'raw', attrs: { text: `[^${label ?? ''}]` } })
        break
      }
      case 'html_inline':
        // tasklists 가 끼워 넣은 체크박스는 목록 쪽에서 이미 읽었다. 나머지
        // 원시 HTML 은 방언이 꺼 두었으므로 글자 그대로 남긴다.
        if (!token.content.includes('task-list-item-checkbox')) {
          out.push(withMarks({ type: 'text', text: token.content }, marks))
        }
        break
      default: {
        const mark = MARK_BY_TAG[token.tag]
        if (!mark) {
          if (token.content) out.push(withMarks({ type: 'text', text: token.content }, marks))
          break
        }
        if (token.nesting === 1) marks.push({ type: mark })
        else dropMark(marks, mark)
      }
    }
  }
  return merged(out)
}

function withMarks(node: TextNode, marks: Mark[]): TextNode {
  return marks.length > 0 ? { ...node, marks: marks.map((m) => ({ ...m })) } : node
}

function dropMark(marks: Mark[], type: MarkType): void {
  for (let i = marks.length - 1; i >= 0; i -= 1) {
    if (marks[i]?.type === type) {
      marks.splice(i, 1)
      return
    }
  }
}

/** 같은 마크를 가진 이웃 텍스트를 합친다. 안 합치면 커서가 조각마다 걸린다. */
function merged(nodes: DocNode[]): DocNode[] {
  const out: DocNode[] = []
  for (const node of nodes) {
    const last = out[out.length - 1]
    if (isText(node) && last !== undefined && isText(last) && sameMarks(last.marks, node.marks)) {
      out[out.length - 1] = { ...last, text: last.text + node.text }
      continue
    }
    out.push(node)
  }
  return out
}

function sameMarks(a: Mark[] | undefined, b: Mark[] | undefined): boolean {
  return JSON.stringify(a ?? []) === JSON.stringify(b ?? [])
}

// ── 문서 → 마크다운 ──────────────────────────────────────────────

/**
 * 편집 문서를 마크다운으로.
 *
 * 서버의 `normalize()` 가 저장할 때 목록 마커·표 여백을 다시 맞추므로
 * 여기서는 **의미가 같은** 마크다운이면 된다. 정규화를 흉내 내려 들면
 * 서버 구현을 클라이언트에 한 벌 더 두게 된다.
 */
export function toMarkdown(doc: ElementNode): string {
  return blocksToMarkdown(doc.content ?? [], '').replace(/\n{3,}/g, '\n\n').trim()
}

function blocksToMarkdown(nodes: DocNode[], indent: string): string {
  return nodes.map((node) => blockToMarkdown(node, indent)).filter((s) => s !== '').join('\n\n')
}

function blockToMarkdown(node: DocNode, indent: string): string {
  if (isText(node)) return indent + node.text
  const children = node.content ?? []

  switch (node.type) {
    case 'paragraph':
      return prefixLines(inlineToMarkdown(children), indent)
    case 'heading': {
      const level = (node.attrs as { level?: number } | undefined)?.level ?? 1
      return `${indent}${'#'.repeat(Math.min(Math.max(level, 1), 6))} ${inlineToMarkdown(children)}`
    }
    case 'bulletList':
      return listToMarkdown(node, children, indent, () => '- ')
    case 'orderedList': {
      const start = (node.attrs as { start?: number } | undefined)?.start ?? 1
      return listToMarkdown(node, children, indent, (i) => `${String(start + i)}. `)
    }
    case 'taskList':
      return listToMarkdown(node, children, indent, (_i, item) => {
        const checked = (item.attrs as { checked?: boolean } | undefined)?.checked === true
        return `- [${checked ? 'x' : ' '}] `
      })
    case 'listItem':
    case 'taskItem':
      // 목록이 마커를 붙여 준다. 홀로 오면 그냥 블록들이다.
      return blocksToMarkdown(children, indent)
    case 'blockquote':
      return prefixLines(blocksToMarkdown(children, ''), `${indent}> `, `${indent}>`)
    case 'codeBlock': {
      const language = (node.attrs as { language?: string | null } | undefined)?.language
      const body = children.map((c) => (isText(c) ? c.text : '')).join('')
      return prefixLines(`\`\`\`${language ?? ''}\n${body}\n\`\`\``, indent)
    }
    case 'horizontalRule':
      return `${indent}---`
    case 'table':
      return tableToMarkdown(children, indent)
    case 'image':
      return indent + inlineToMarkdown([node])
    case 'verbatim':
      return prefixLines((node.attrs as { source?: string } | undefined)?.source ?? '', indent)
    default:
      return blocksToMarkdown(children, indent)
  }
}

function listToMarkdown(
  list: ElementNode,
  items: DocNode[],
  indent: string,
  marker: (index: number, item: ElementNode) => string,
): string {
  // 촘촘한 목록은 항목 사이에도, 항목 안에서도 빈 줄을 두지 않는다. 빈 줄
  // 하나가 목록 전체를 성글게 만들어 `<li>` 안에 `<p>` 가 생긴다.
  const tight = (list.attrs as { tight?: boolean } | undefined)?.tight !== false
  const gap = tight ? '\n' : '\n\n'
  return items
    .map((item, index) => {
      if (isText(item)) return indent + item.text
      const bullet = marker(index, item)
      const body = (item.content ?? [])
        .map((child) => blockToMarkdown(child, ''))
        .filter((text) => text !== '')
        .join(gap)
      // 이어지는 줄은 마커 폭만큼 들여쓴다. 안 그러면 목록에서 빠져나온다.
      const pad = ' '.repeat(bullet.length)
      const lines = body.split('\n')
      return lines
        .map((line, i) => (i === 0 ? `${indent}${bullet}${line}` : line === '' ? '' : `${indent}${pad}${line}`))
        .join('\n')
    })
    .join(gap)
}

function tableToMarkdown(rows: DocNode[], indent: string): string {
  const grid: string[][] = []
  const aligns: (string | null)[] = []

  rows.forEach((row, rowIndex) => {
    if (isText(row)) return
    const cells = (row.content ?? []).map((cell) => {
      if (isText(cell)) return escapePipes(cell.text)
      if (rowIndex === 0) {
        aligns.push((cell.attrs as { align?: string } | undefined)?.align ?? null)
      }
      // 셀 안의 개행은 표를 깨뜨린다. 한 줄로 만든다.
      return escapePipes(
        blocksToMarkdown(cell.content ?? [], '').replace(/\s*\n\s*/g, ' ').trim(),
      )
    })
    grid.push(cells)
  })

  if (grid.length === 0) return ''
  const width = Math.max(...grid.map((row) => row.length))
  const line = (cells: string[]) =>
    `${indent}| ${Array.from({ length: width }, (_, i) => cells[i] ?? '').join(' | ')} |`

  const divider = `${indent}| ${Array.from({ length: width }, (_, i) => dashes(aligns[i] ?? null)).join(' | ')} |`
  return [line(grid[0] as string[]), divider, ...grid.slice(1).map((row) => line(row))].join('\n')
}

/**
 * 표 셀 안의 막대를 이스케이프한다.
 *
 * **안 하면 그 자리에서 셀이 갈라진다.** `a | b` 한 칸이 두 칸이 되어 그
 * 줄의 나머지가 한 칸씩 밀리고, 헤더 폭을 넘어간 값은 어느 파서에서든
 * 버려진다 — 표 하나가 통째로 어긋난 채 저장되고, 되돌릴 원본은 이미 없다.
 *
 * GFM 에서 표 안에 진짜 막대를 넣는 방법은 `\|` 하나뿐이고, **코드 스팬
 * 안에서도 그렇다**(표를 나누는 일이 인라인 코드보다 먼저 일어난다).
 */
function escapePipes(cell: string): string {
  return cell.replace(/\|/g, '\\|')
}

function dashes(align: string | null): string {
  switch (align) {
    case 'left':
      return ':---'
    case 'right':
      return '---:'
    case 'center':
      return ':---:'
    default:
      return '---'
  }
}

/** 여러 줄에 같은 접두사를 붙인다. 빈 줄에는 공백을 남기지 않는다. */
function prefixLines(text: string, prefix: string, blankPrefix = prefix.trimEnd()): string {
  if (prefix === '') return text
  return text
    .split('\n')
    .map((line) => (line === '' ? blankPrefix : prefix + line))
    .join('\n')
}

/**
 * 다시 읽었을 때 뜻이 달라질 글자만 막는다.
 *
 * `]` 는 여는 대괄호만 막으면 링크가 안 되므로 그냥 둔다. `_` 는 낱말 안에서
 * 강조가 아니라서(CommonMark) 경계에 있을 때만 막는다 — `snake_case_name` 을
 * `snake\_case\_name` 으로 만들면 소스 모드가 읽기 나빠진다. 덜 막으면
 * 라운드트립이 깨지고, 다 막으면 사람이 읽을 수 없게 된다.
 */
function escapeText(text: string): string {
  return text.replace(/([\\`*[])/g, '\\$1').replace(/(^|[\s\p{P}])_/gu, '$1\\_')
}

function inlineToMarkdown(nodes: DocNode[]): string {
  return nodes.map(inlineNodeToMarkdown).join('')
}

function inlineNodeToMarkdown(node: DocNode): string {
  if (!isText(node)) {
    const element = node
    if (element.type === 'hardBreak') return '\\\n'
    if (element.type === 'raw') return (element.attrs as { text?: string } | undefined)?.text ?? ''
    if (element.type === 'image') {
      const attrs = (element.attrs ?? {}) as { src?: string; alt?: string; title?: string | null }
      const title = attrs.title ? ` "${attrs.title}"` : ''
      return `![${attrs.alt ?? ''}](${attrs.src ?? ''}${title})`
    }
    return inlineToMarkdown(element.content ?? [])
  }

  const marks = node.marks ?? []
  // 코드는 안쪽 글자를 이스케이프하지 않는다. 백틱 사이는 글자 그대로다.
  const code = marks.some((m) => m.type === 'code')
  let text = code ? node.text : escapeText(node.text)
  if (code) text = `\`${text}\``
  if (marks.some((m) => m.type === 'strike')) text = `~~${text}~~`
  if (marks.some((m) => m.type === 'italic')) text = `*${text}*`
  if (marks.some((m) => m.type === 'bold')) text = `**${text}**`

  const link = marks.find((m) => m.type === 'link')
  if (link?.attrs) {
    const title = link.attrs.title ? ` "${link.attrs.title}"` : ''
    text = `[${text}](${link.attrs.href}${title})`
  }
  return text
}

/** 한 바퀴 돌린 마크다운. 편집기를 거쳤을 때 무엇이 되는지가 이것이다. */
export function cycle(markdown: string): string {
  return toMarkdown(toDoc(markdown))
}

/** 첫 텍스트 노드 앞의 공백 한 칸을 없앤다. */
function trimLeadingSpace(blocks: DocNode[]): void {
  const first = blocks[0]
  if (!first || isText(first)) return
  const inline = first.content?.[0]
  if (!inline || !isText(inline)) return
  if (!inline.text.startsWith(' ')) return
  first.content = [{ ...inline, text: inline.text.slice(1) }, ...(first.content ?? []).slice(1)]
}
