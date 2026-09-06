/**
 * 디렉티브 = 매크로 (wiki-markdown.md 3절) — 클라이언트 쪽.
 *
 * 서버(`core/markdown/directives.py`)가 저장 시점에 같은 문법으로 인자를
 * 검사한다. 여기서는 **본문을 조각으로 나누는 일**만 한다. 데이터가 필요한
 * 디렉티브(`::children`·`::issues`)는 리액트 컴포넌트가 되어야 하므로
 * 마크다운 HTML 안에 섞어 그릴 수 없다.
 *
 * 모르는 이름은 조각으로 떼지 않는다 — 그냥 마크다운 텍스트로 남는다.
 * 그게 "미지원 뷰어에서도 텍스트로 읽힌다"는 성질이다.
 */

/** 데이터가 필요해 컴포넌트로 그리는 리프 디렉티브. */
export const LEAF_NAMES = ['toc', 'children', 'issues', 'excerpt'] as const
/** 강조 상자. 이름이 곧 톤이다. */
export const CONTAINER_NAMES = ['info', 'note', 'tip', 'warning', 'danger'] as const

export type LeafName = (typeof LEAF_NAMES)[number]
export type ContainerName = (typeof CONTAINER_NAMES)[number]

const LEAF_RE = /^::([a-zA-Z][a-zA-Z0-9_-]*)(?:\{([^}]*)\})?[ \t]*$/
const CONTAINER_OPEN_RE = /^(:{3,})([a-zA-Z][a-zA-Z0-9_-]*)(?:\{([^}]*)\})?[ \t]*$/
const ATTR_RE = /([a-zA-Z][a-zA-Z0-9_-]*)(?:=(?:"([^"]*)"|([^\s"}]+)))?/g
const FENCE_RE = /^(```|~~~)/

export interface MarkdownSegment {
  kind: 'markdown'
  text: string
}

export interface DirectiveSegment {
  kind: 'directive'
  name: string
  attrs: Record<string, string>
  /** 컨테이너면 감싼 본문. 리프면 null. */
  body: string | null
}

export type Segment = MarkdownSegment | DirectiveSegment

export function parseAttrs(raw: string): Record<string, string> {
  const attrs: Record<string, string> = {}
  for (const match of raw.matchAll(ATTR_RE)) {
    const key = (match[1] ?? '').toLowerCase()
    if (!key) continue
    attrs[key] = match[2] ?? match[3] ?? ''
  }
  return attrs
}

function isLeaf(name: string): boolean {
  return (LEAF_NAMES as readonly string[]).includes(name)
}

function isContainer(name: string): boolean {
  return (CONTAINER_NAMES as readonly string[]).includes(name)
}

/**
 * 본문을 마크다운 조각과 디렉티브로 나눈다.
 *
 * 코드 펜스 안은 건드리지 않는다 — 거기 있는 `::toc` 는 예제다.
 */
export function splitDirectives(source: string): Segment[] {
  const segments: Segment[] = []
  const lines = source.split('\n')
  let buffer: string[] = []
  let fence: string | null = null

  const flush = () => {
    const text = buffer.join('\n')
    if (text.trim()) segments.push({ kind: 'markdown', text })
    buffer = []
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] as string

    if (fence !== null) {
      buffer.push(line)
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    const opening = FENCE_RE.exec(line.trimStart())
    if (opening) {
      fence = opening[1] as string
      buffer.push(line)
      continue
    }

    const container = CONTAINER_OPEN_RE.exec(line)
    if (container && isContainer((container[2] as string).toLowerCase())) {
      const marker = container[1] as string
      const end = findClose(lines, i + 1, marker)
      // 닫는 줄이 없으면 상자가 아니다. 문서 나머지를 삼키면 안 된다.
      if (end !== -1) {
        flush()
        segments.push({
          kind: 'directive',
          name: (container[2] as string).toLowerCase(),
          attrs: parseAttrs(container[3] ?? ''),
          body: lines.slice(i + 1, end).join('\n'),
        })
        i = end
        continue
      }
    }

    const leaf = LEAF_RE.exec(line)
    if (leaf && isLeaf((leaf[1] as string).toLowerCase())) {
      flush()
      segments.push({
        kind: 'directive',
        name: (leaf[1] as string).toLowerCase(),
        attrs: parseAttrs(leaf[2] ?? ''),
        body: null,
      })
      continue
    }

    buffer.push(line)
  }
  flush()
  return segments
}

function findClose(lines: string[], from: number, marker: string): number {
  for (let i = from; i < lines.length; i += 1) {
    const trimmed = (lines[i] as string).trim()
    // 여는 것과 같은 길이 이상의 콜론만 닫는다. 그래야 중첩이 된다.
    if (/^:+$/.test(trimmed) && trimmed.length >= marker.length) return i
  }
  return -1
}

export interface Heading {
  level: number
  text: string
  /** `#` 링크로 쓸 값. 렌더러가 붙이는 id 와 같아야 한다. */
  slug: string
}

/**
 * `::toc` 가 쓸 제목 목록.
 *
 * 렌더된 HTML 이 아니라 원문에서 뽑는다. HTML 을 파싱하면 목차를 그리려고
 * 문서를 두 번 그리게 된다. 코드 펜스 안의 `#` 은 제목이 아니다.
 */
export function collectHeadings(source: string, maxDepth = 6): Heading[] {
  const headings: Heading[] = []
  const seen = new Map<string, number>()
  let fence: string | null = null

  for (const line of source.split('\n')) {
    if (fence !== null) {
      if (line.trimStart().startsWith(fence)) fence = null
      continue
    }
    const opening = FENCE_RE.exec(line.trimStart())
    if (opening) {
      fence = opening[1] as string
      continue
    }
    const match = /^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/.exec(line)
    if (!match) continue
    const level = (match[1] as string).length
    if (level > maxDepth) continue
    const text = (match[2] as string).trim()
    headings.push({ level, text, slug: uniqueSlug(text, seen) })
  }
  return headings
}

export function headingSlug(text: string): string {
  return (
    text
      .trim()
      .toLowerCase()
      // 경로·URL 에서 의미를 갖는 문자만 뺀다. 한글은 그대로 둔다 —
      // 로마자로 옮기면 원래 제목을 되짚을 수 없다 (slug.py 와 같은 규칙).
      .replace(/[^\p{L}\p{N}\s-]/gu, '')
      .replace(/[\s_]+/g, '-')
      .replace(/-{2,}/g, '-')
      .replace(/^-|-$/g, '') || 'section'
  )
}

function uniqueSlug(text: string, seen: Map<string, number>): string {
  const base = headingSlug(text)
  const count = seen.get(base) ?? 0
  seen.set(base, count + 1)
  return count === 0 ? base : `${base}-${String(count + 1)}`
}

/**
 * `::excerpt` 가 보여 줄 앞부분 — 문서의 첫 문단.
 *
 * 앞부분을 표시하는 별도 문법(`<!-- more -->` 같은 것)을 새로 만들지 않는다.
 * 문서마다 표시를 달아야 인용이 되는 기능은 아무도 쓰지 않는다. 첫 문단은
 * 사람이 이미 요약으로 쓰고 있는 자리다.
 *
 * 제목·디렉티브·코드 펜스·front matter 는 건너뛴다 — 제목만 뜨는 인용은
 * 원문 링크보다 나을 게 없다.
 */
export function firstParagraph(source: string): string {
  const lines = source.split('\n')
  const paragraph: string[] = []
  let index = 0

  // front matter 는 문서 첫 줄에서만 인정한다 (dialect.ts 와 같은 규칙).
  if (lines[0]?.trimEnd() === '---') {
    const close = lines.findIndex((line, i) => i > 0 && line.trimEnd() === '---')
    if (close > 0) index = close + 1
  }

  for (; index < lines.length; index += 1) {
    const line = lines[index] as string
    const trimmed = line.trim()

    if (paragraph.length === 0) {
      if (trimmed === '') continue
      // 코드 펜스는 통째로 건너뛴다. 안쪽 글자가 문단처럼 보일 수 있다.
      const fence = FENCE_RE.exec(trimmed)
      if (fence) {
        const marker = fence[1] as string
        index += 1
        while (index < lines.length && !(lines[index] as string).trimStart().startsWith(marker)) {
          index += 1
        }
        continue
      }
      if (/^#{1,6}[ \t]/.test(trimmed)) continue
      if (trimmed.startsWith('::')) continue
      if (/^([-*_])\1{2,}$/.test(trimmed.replace(/\s/g, ''))) continue
    }

    if (trimmed === '') break
    paragraph.push(trimmed)
  }
  return paragraph.join(' ')
}
