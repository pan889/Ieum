/**
 * 붙여넣은 HTML → 마크다운.
 *
 * 사람은 위키·구글 문서·메일·스프레드시트에서 복사해 온다. 그걸 평문으로
 * 떨어뜨리면 제목·표·링크가 통째로 사라지고, 원시 HTML 로 저장하면 방언이
 * `html: false` 로 막아 둔 것을 우회하게 된다 (wiki-markdown 3절). 그래서
 * **마크다운으로 바꿔서** 넣는다 — 정본은 언제나 마크다운이다(ADR-0008).
 *
 * 직렬화는 `doc.ts` 의 `toMarkdown` 을 그대로 쓴다. 여기서 마크다운 문자열을
 * 직접 조립하면 이스케이프·표 여백·목록 촘촘함 규칙이 두 벌이 되고, 두 벌은
 * 반드시 갈라진다. 이 파일이 하는 일은 DOM 을 **같은 문서 모델**로 옮기는
 * 것뿐이다. 그래서 소스 모드와 서식 모드가 같은 것을 붙여넣는다.
 */

import type { DocNode, ElementNode, Mark, MarkType, TextNode } from './doc'
import { toMarkdown } from './doc'
import { internalLabel, internalUri } from './paste'

/** 통째로 버린다. 화면에 안 보이는 것들이다. */
const DROPPED = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'HEAD', 'TITLE', 'META', 'LINK', 'INPUT'])

/** 그 자체로 블록인 태그. 이게 자식에 있으면 감싼 `div` 는 그냥 껍데기다. */
const BLOCKS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DIV', 'DL', 'FIELDSET', 'FIGCAPTION',
  'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR',
  'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'UL',
])

const MARK_TAGS: Record<string, MarkType> = {
  STRONG: 'bold',
  B: 'bold',
  EM: 'italic',
  I: 'italic',
  S: 'strike',
  DEL: 'strike',
  STRIKE: 'strike',
  CODE: 'code',
}

/**
 * 손댈 만한 것이 들어 있는가.
 *
 * 편집기에서 코드를 복사하면 색깔만 입힌 `div`·`span` 더미가 온다. 그것까지
 * 변환하면 줄이 문단으로 흩어지고 들여쓰기가 사라진다 — 코드를 붙여넣은
 * 사람에게는 최악이다. 구조나 링크가 실제로 있을 때만 손댄다.
 *
 * `p` 는 세고 `div` 는 안 센다. 문단은 사람이 문단으로 쓴 것이지만, `div` 는
 * 무엇이든 감싸는 데 쓰는 상자라서 있다는 사실만으로는 아무 뜻도 없다.
 */
const RICH = 'p,a[href],h1,h2,h3,h4,h5,h6,ul,ol,table,pre,code,blockquote,img,strong,b,em,i,del,s'

/**
 * 편집기가 자기 조각을 복사한 것.
 *
 * ProseMirror 는 열린 조각(문단 중간부터 잘린 것)을 `data-pm-slice` 에 적어
 * 두고, 붙여넣을 때 그걸 보고 이어 붙인다. 우리 변환을 거치면 그 정보가
 * 사라져서 문장 중간을 복사해도 새 문단이 된다. 서식 모드끼리는 편집기가
 * 하게 둔다.
 */
export function copiedFromEditor(html: string): boolean {
  return html.includes('data-pm-slice')
}

/** 붙여넣은 HTML 을 마크다운으로. 우리가 손댈 것이 아니면 null. */
export function pastedMarkdown(html: string): string | null {
  if (html.trim() === '') return null
  const body = parse(html)
  if (!body.querySelector(RICH)) return null
  const markdown = toMarkdown({ type: 'doc', content: blocks(body) })
  return markdown === '' ? null : markdown
}

/** 조건 없이 변환한다. 테스트와, 이미 걸러 낸 자리에서 쓴다. */
export function htmlToMarkdown(html: string): string {
  return toMarkdown({ type: 'doc', content: blocks(parse(html)) })
}

function parse(html: string): HTMLElement {
  return new DOMParser().parseFromString(html, 'text/html').body
}

// ── 블록 ────────────────────────────────────────────────────────

/**
 * 자식들을 블록 목록으로.
 *
 * 블록 사이에 흩어진 글자(`<div>` 없이 놓인 텍스트, `<span>` 조각)는 모아
 * 두었다가 다음 블록을 만나면 문단 하나로 내놓는다. 안 그러면 구글 문서처럼
 * 껍데기가 많은 HTML 에서 글자가 통째로 사라진다.
 */
function blocks(parent: ParentNode, marks: Mark[] = []): DocNode[] {
  const out: DocNode[] = []
  let pending: DocNode[] = []

  const flush = () => {
    const trimmed = trimInline(pending)
    pending = []
    if (trimmed.length > 0) out.push({ type: 'paragraph', content: trimmed })
  }

  for (const child of parent.childNodes) {
    if (child.nodeType === 3) {
      pending.push(...textNodes(child.nodeValue ?? '', marks))
      continue
    }
    if (child.nodeType !== 1) continue
    const element = child as HTMLElement
    if (DROPPED.has(element.tagName)) continue

    const block = blockNode(element, marks)
    if (block === null) {
      pending.push(...inlineNodes(element, marksOf(element, marks)))
      continue
    }
    flush()
    if (Array.isArray(block)) out.push(...block)
    else out.push(block)
  }
  flush()
  return out
}

/** 블록 하나. 인라인으로 다뤄야 하면 null. */
function blockNode(element: HTMLElement, marks: Mark[]): DocNode | DocNode[] | null {
  const tag = element.tagName
  const inherited = marksOf(element, marks)
  if (/^H[1-6]$/.test(tag)) {
    return {
      type: 'heading',
      attrs: { level: Number(tag.slice(1)) },
      // 제목은 이미 굵다. `<span style="font-weight:700">` 로 굵기를 준 원문
      // (구글 문서가 그렇다)을 그대로 옮기면 `## **제목**` 이 된다.
      content: stripUniform(inlineOf(element, inherited), 'bold'),
    }
  }
  switch (tag) {
    case 'P':
      return paragraphOf(element, inherited)
    case 'UL':
    case 'OL':
      return listNode(element, inherited)
    case 'BLOCKQUOTE':
      return { type: 'blockquote', content: blocksOrParagraph(element, inherited) }
    case 'PRE':
      return codeBlockNode(element)
    case 'HR':
      return { type: 'horizontalRule' }
    case 'TABLE':
      return tableNode(element as HTMLTableElement, inherited)
    case 'BR':
      // 블록 사이에 홀로 놓인 줄바꿈은 빈 문단이다. 버린다.
      return []
    default:
      break
  }
  // 안에 블록이 있으면 껍데기다 — 파고든다. 태그 이름으로만 가리면 안 된다:
  // 구글 문서는 문서 전체를 `<b style="font-weight:normal">` 로 감싸고,
  // 스프레드시트는 `<google-sheets-html-origin>` 으로 감싼다. 인라인으로
  // 읽어 버리면 제목도 목록도 표도 한 문단으로 뭉개진다.
  if (hasBlockChild(element)) return blocks(element, inherited)
  return BLOCKS.has(tag) || tag === 'BODY' ? paragraphOf(element, inherited) : null
}

function hasBlockChild(element: HTMLElement): boolean {
  for (const child of element.children) {
    if (BLOCKS.has(child.tagName)) return true
    // 껍데기가 여러 겹일 수 있다 — 구글 문서는 `<b><h1>` 을 또 감싼다.
    if (!INLINE_ONLY.has(child.tagName) && hasBlockChild(child as HTMLElement)) return true
  }
  return false
}

/** 안에 블록이 들어갈 수 없는 태그. 여기서 멈춰야 한없이 파고들지 않는다. */
const INLINE_ONLY = new Set(['A', 'CODE', 'BR', 'IMG', 'INPUT', 'SUB', 'SUP'])

/** 문단 하나. 빈 문단은 안 만든다 — 빈 줄만 늘어난다. */
function paragraphOf(element: HTMLElement, marks: Mark[]): DocNode | DocNode[] {
  const content = inlineOf(element, marks)
  return content.length === 0 ? [] : { type: 'paragraph', content }
}

function inlineOf(element: HTMLElement, marks: Mark[] = []): DocNode[] {
  return trimInline(inlineNodes(element, marks))
}

/** 블록이 섞여 있으면 블록으로, 아니면 문단 하나로. */
function blocksOrParagraph(element: HTMLElement, marks: Mark[] = []): DocNode[] {
  if (hasBlockChild(element)) return blocks(element, marks)
  const content = inlineOf(element, marks)
  return content.length === 0 ? [] : [{ type: 'paragraph', content }]
}

/** 글자 전부에 붙은 마크를 걷어낸다. 일부에만 붙은 것은 뜻이 있으므로 둔다. */
function stripUniform(nodes: DocNode[], type: MarkType): DocNode[] {
  const texts = nodes.filter((node) => 'text' in node)
  if (texts.length === 0) return nodes
  if (!texts.every((node) => (node.marks ?? []).some((mark) => mark.type === type))) return nodes
  return nodes.map((node) =>
    'text' in node ? { ...node, marks: (node.marks ?? []).filter((m) => m.type !== type) } : node,
  )
}

function codeBlockNode(element: HTMLElement): ElementNode {
  const inner = element.querySelector('code')
  const source = element.textContent
  return {
    type: 'codeBlock',
    attrs: { language: languageOf(inner ?? element) },
    content: source === '' ? [] : [{ type: 'text', text: source.replace(/\n+$/, '') }],
  }
}

/** `class="language-ts"`·`lang-ts`. 못 찾으면 언어 없는 울타리다. */
function languageOf(element: HTMLElement): string | null {
  const found = /(?:^|\s)(?:language|lang|highlight)-([\w+#-]+)/.exec(element.className)
  return found ? (found[1] as string) : null
}

function listNode(element: HTMLElement, marks: Mark[] = []): ElementNode {
  const ordered = element.tagName === 'OL'
  const items: DocNode[] = []
  let tasks = false
  // HTML 목록에는 촘촘함이 없다. **덩이가 둘 이상인 항목**이 있을 때만
  // 성글게 본다. 항목 하나에 문단 하나인 `<li><p>` 를 성근 목록으로 읽으면
  // 구글 문서에서 가져온 목록이 전부 빈 줄로 벌어진다 — 원본은 붙어 있는데.
  let tight = true

  for (const child of element.children) {
    if (child.tagName !== 'LI') continue
    const item = child as HTMLElement
    const checkbox = item.querySelector(':scope > input[type=checkbox]')
    if (checkbox !== null) tasks = true
    const content = blocksOrParagraph(item, marks)
    if (content.length > 1) tight = false
    items.push({
      type: checkbox === null ? 'listItem' : 'taskItem',
      ...(checkbox === null ? {} : { attrs: { checked: (checkbox as HTMLInputElement).checked } }),
      content,
    })
  }

  const start = ordered ? Number(element.getAttribute('start') ?? 1) : 1
  return {
    type: tasks ? 'taskList' : ordered ? 'orderedList' : 'bulletList',
    attrs: { tight, ...(ordered && start !== 1 ? { start } : {}) },
    content: items,
  }
}

function tableNode(table: HTMLTableElement, marks: Mark[] = []): ElementNode {
  const rows: DocNode[] = []
  for (const row of table.rows) {
    const cells: DocNode[] = []
    for (const cell of row.cells) {
      const align = alignOf(cell)
      cells.push({
        type: cell.tagName === 'TH' ? 'tableHeader' : 'tableCell',
        ...(align ? { attrs: { align } } : {}),
        // 칸 안에서도 굵게는 굵게다. 다만 머리 줄은 이미 굵으므로 걷어낸다.
        content: [
          {
            type: 'paragraph',
            content:
              cell.tagName === 'TH'
                ? stripUniform(inlineOf(cell, marks), 'bold')
                : inlineOf(cell, marks),
          },
        ],
      })
    }
    if (cells.length > 0) rows.push({ type: 'tableRow', content: cells })
  }
  return { type: 'table', content: rows }
}

function alignOf(cell: HTMLTableCellElement): string | null {
  const found = /text-align:\s*(left|center|right)/i.exec(cell.getAttribute('style') ?? '')
  if (found) return (found[1] as string).toLowerCase()
  const attr = cell.getAttribute('align')
  return attr && /^(left|center|right)$/i.test(attr) ? attr.toLowerCase() : null
}

// ── 인라인 ──────────────────────────────────────────────────────

function inlineNodes(parent: ParentNode, marks: Mark[]): DocNode[] {
  const out: DocNode[] = []
  for (const child of parent.childNodes) {
    if (child.nodeType === 3) {
      out.push(...textNodes(child.nodeValue ?? '', marks))
      continue
    }
    if (child.nodeType !== 1) continue
    const element = child as HTMLElement
    if (DROPPED.has(element.tagName)) continue

    switch (element.tagName) {
      case 'BR':
        out.push({ type: 'hardBreak' })
        continue
      case 'IMG':
        out.push(imageNode(element as HTMLImageElement))
        continue
      case 'A':
        out.push(...anchorNodes(element as HTMLAnchorElement, marks))
        continue
      default:
        out.push(...inlineNodes(element, marksOf(element, marks)))
    }
  }
  return merge(out)
}

function imageNode(image: HTMLImageElement): ElementNode {
  return {
    type: 'image',
    attrs: {
      // `src` 프로퍼티는 브라우저가 절대 주소로 바꿔 놓는다. 적힌 그대로 쓴다.
      src: image.getAttribute('src') ?? '',
      alt: image.getAttribute('alt') ?? '',
      title: image.getAttribute('title'),
    },
  }
}

/**
 * 링크.
 *
 * 우리 주소는 스킴으로 바꾼다 — 호스트가 바뀌는 순간 전부 죽고, 내보낸
 * `.md` 가 그 서버에 묶인다 (paste.ts). 링크 글자가 주소 그대로면 사람이
 * 읽는 이름으로 바꿔 준다.
 */
function anchorNodes(anchor: HTMLAnchorElement, marks: Mark[]): DocNode[] {
  const raw = anchor.getAttribute('href') ?? ''
  if (raw === '') return inlineNodes(anchor, marks)
  const internal = internalUri(raw)
  const href = internal ?? raw
  const title = anchor.getAttribute('title')
  const link: Mark = { type: 'link', attrs: { href, ...(title ? { title } : {}) } }

  const inner = inlineNodes(anchor, [...marksOf(anchor, marks), link])
  const text = plainText(inner)
  if (internal && text.trim() === raw.trim()) {
    return [{ type: 'text', text: internalLabel(internal), marks: [link] }]
  }
  return text.trim() === '' ? [] : inner
}

function plainText(nodes: DocNode[]): string {
  return nodes.map((node) => ('text' in node ? node.text : '')).join('')
}

/** 태그와 인라인 스타일에서 마크를 읽는다. */
function marksOf(element: HTMLElement, inherited: Mark[]): Mark[] {
  const marks = [...inherited]
  const tag = MARK_TAGS[element.tagName]
  if (tag) add(marks, tag)

  const style = element.getAttribute('style') ?? ''
  const weight = /font-weight:\s*([\w]+)/i.exec(style)?.[1]
  if (weight !== undefined) {
    // 구글 문서는 붙여넣기 전체를 `<b style="font-weight:normal">` 로 감싼다.
    // 태그만 보면 문서가 통째로 굵어진다.
    if (weight === 'bold' || weight === 'bolder' || Number(weight) >= 600) add(marks, 'bold')
    else drop(marks, 'bold')
  }
  const italic = /font-style:\s*([\w]+)/i.exec(style)?.[1]
  if (italic !== undefined) {
    if (italic === 'italic' || italic === 'oblique') add(marks, 'italic')
    else drop(marks, 'italic')
  }
  if (/text-decoration[^;]*:\s*[^;]*line-through/i.test(style)) add(marks, 'strike')
  return marks
}

function add(marks: Mark[], type: MarkType): void {
  if (!marks.some((mark) => mark.type === type)) marks.push({ type })
}

function drop(marks: Mark[], type: MarkType): void {
  for (let i = marks.length - 1; i >= 0; i -= 1) if (marks[i]?.type === type) marks.splice(i, 1)
}

/**
 * HTML 의 공백은 몇 칸이든 한 칸이다. 그대로 옮기면 원문의 줄바꿈과 들여쓰기가
 * 마크다운에 그대로 들어가 목록이 코드 블록이 된다. `&nbsp;` 도 같이 편다 —
 * 워드에서 온 글이 온통 붙어 있는 것처럼 보이는 게 그것 때문이다.
 */
function textNodes(raw: string, marks: Mark[]): DocNode[] {
  // `\s` 는 `&nbsp;`(U+00A0)도 잡는다.
  const text = raw.replace(/\s+/g, ' ')
  if (text === '') return []
  const node: TextNode = { type: 'text', text }
  return [marks.length > 0 ? { ...node, marks: marks.map((mark) => ({ ...mark })) } : node]
}

/** 문단 앞뒤의 공백은 뜻이 없다. 가운데 공백은 낱말을 띄우므로 남긴다. */
function trimInline(nodes: DocNode[]): DocNode[] {
  const out = [...nodes]
  while (out.length > 0) {
    const first = out[0] as DocNode
    if (!('text' in first)) break
    const text = first.text.replace(/^ +/, '')
    if (text === '') out.shift()
    else {
      out[0] = { ...first, text }
      break
    }
  }
  while (out.length > 0) {
    const last = out[out.length - 1] as DocNode
    if (!('text' in last)) break
    const text = last.text.replace(/ +$/, '')
    if (text === '') out.pop()
    else {
      out[out.length - 1] = { ...last, text }
      break
    }
  }
  return out
}

/** 같은 마크를 가진 이웃 글자를 합친다. 조각난 채 두면 `**a****b**` 가 된다. */
function merge(nodes: DocNode[]): DocNode[] {
  const out: DocNode[] = []
  for (const node of nodes) {
    const last = out[out.length - 1]
    if (
      last !== undefined &&
      'text' in node &&
      'text' in last &&
      JSON.stringify(last.marks ?? []) === JSON.stringify(node.marks ?? [])
    ) {
      out[out.length - 1] = { ...last, text: last.text + node.text }
      continue
    }
    out.push(node)
  }
  return out
}
