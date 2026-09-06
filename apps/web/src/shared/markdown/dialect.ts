import MarkdownIt from 'markdown-it'
import footnote from 'markdown-it-footnote'
import taskLists from 'markdown-it-task-lists'

import { CONTAINER_NAMES, headingSlug } from './directives'

/**
 * IFM(ieum Flavored Markdown) 방언 — 클라이언트 쪽.
 *
 * 정본 명세는 `packages/markdown/dialect.json` 이고 서버(`core/markdown/
 * dialect.py`)와 여기가 각자 옮겨 적는다. 런타임에 그 파일을 읽지 않는
 * 이유는 서버·클라이언트 번들에 저장소 레이아웃이 따라가지 않기 때문이다.
 * 대신 **양쪽 테스트가 자기 설정과 명세가 같은지 확인**한다 — 한쪽만
 * 고치면 CI 가 잡는다 (docs/architecture/wiki-markdown.md 1절).
 *
 * `html: false` 가 여기서 제일 중요한 줄이다. 켜는 순간 이슈 설명 한 줄로
 * XSS 가 된다. 이 값을 바꾸려면 새니타이저부터 붙여야 한다.
 */
export const VERSION = 3
export const PRESET = 'commonmark'
export const OPTIONS = {
  html: false,
  linkify: true,
  typographer: false,
  breaks: false,
} as const
export const CORE_RULES = ['table', 'strikethrough'] as const
export const PLUGINS = ['front_matter', 'tasklists', 'footnote', 'colon_fence'] as const
export const LINK_SCHEMES = [
  'http',
  'https',
  'mailto',
  'attachment',
  'page',
  'issue',
  // 멘션은 `[@Alice](user:<uuid>)` 로 저장한다 — 이름이 바뀌어도 안 깨진다.
  'user',
] as const

/** 이 모듈의 설정을 명세 파일과 같은 모양으로. 테스트가 비교한다. */
export function asSpec() {
  return {
    version: VERSION,
    preset: PRESET,
    options: { ...OPTIONS },
    core: [...CORE_RULES],
    plugins: [...PLUGINS],
    linkSchemes: [...LINK_SCHEMES],
  }
}

/**
 * 허용한 스킴만 링크로 만든다.
 *
 * markdown-it 기본 검사도 javascript: 를 막지만, 화이트리스트로 한 번 더
 * 좁힌다 — 상위 기본값이 바뀌어도 우리 정책은 그대로여야 한다.
 */
export function validateLink(url: string): boolean {
  const trimmed = url.trim()
  if (trimmed === '') return false
  if (/^[#/]|^\.\.?\//.test(trimmed)) return true
  const match = /^([a-z][a-z0-9+.-]*):/i.exec(trimmed)
  if (!match) return true
  return (LINK_SCHEMES as readonly string[]).includes((match[1] as string).toLowerCase())
}

/**
 * YAML front matter 를 본문에서 걷어낸다.
 *
 * markdown-it-front-matter 를 쓰지 않는 이유는 그 패키지의 index.d.ts 가
 * markdown-it 14 에 없는 `markdown-it/lib` 를 참조해 타입이 깨지기 때문이다.
 * 하는 일이 20줄이라 직접 둔다 — 서버의 mdit_py_plugins.front_matter 와
 * 같은 규칙이다: **문서 첫 줄**의 `---` 만 front matter 로 본다.
 */
function frontMatterPlugin(md: MarkdownIt): void {
  md.block.ruler.before(
    'table',
    'front_matter',
    (state, startLine, endLine, silent) => {
      if (startLine !== 0) return false
      const begin = state.bMarks[startLine] as number
      if (state.src.slice(begin, begin + 4) !== '---\n') return false

      let line = startLine + 1
      for (; line < endLine; line += 1) {
        const start = state.bMarks[line] as number
        const end = state.eMarks[line] as number
        if (state.src.slice(start, end).trimEnd() === '---') break
      }
      // 닫는 --- 이 없으면 front matter 가 아니다. 문서 전체를 삼키면 안 된다.
      if (line >= endLine) return false
      if (silent) return true

      state.line = line + 1
      return true
    },
    { alt: [] },
  )
}

/**
 * `[@Alice](user:<uuid>)` 를 링크가 아니라 멘션 칩으로 그린다.
 *
 * 링크로 두면 눌렀을 때 `user:` 스킴으로 아무 데도 못 간다. 서버의
 * `to_html` 은 메일용이라 이 규칙이 없다 — 메일에서는 그냥 텍스트로 남는다.
 */
function mentionPlugin(md: MarkdownIt): void {
  const openLink = md.renderer.rules['link_open']
  const closeLink = md.renderer.rules['link_close']
  const mentionDepth: boolean[] = []

  md.renderer.rules['link_open'] = (tokens, idx, options, env, self) => {
    const href = tokens[idx]?.attrGet('href') ?? ''
    if (href.startsWith('user:')) {
      mentionDepth.push(true)
      return '<span class="ieum-mention">'
    }
    mentionDepth.push(false)
    return openLink ? openLink(tokens, idx, options, env, self) : self.renderToken(tokens, idx, options)
  }

  md.renderer.rules['link_close'] = (tokens, idx, options, env, self) => {
    if (mentionDepth.pop() === true) return '</span>'
    return closeLink
      ? closeLink(tokens, idx, options, env, self)
      : self.renderToken(tokens, idx, options)
  }
}

/**
 * `:::info` 컨테이너 — **받침대**다.
 *
 * 보통은 `RichText` 가 문서를 나눌 때 컨테이너를 떼어 컴포넌트로 그린다.
 * 하지만 목록 안처럼 떼어내지 않는 자리에도 컨테이너가 올 수 있다. 규칙이
 * 없으면 markdown-it 이 그런 블록을 통째로 문단으로 흘려서 `:::info` 라는
 * 글자가 화면에 그대로 뜬다.
 *
 * 서버의 `mdit_py_plugins.colon_fence` 와 같은 규칙이다: 여는 콜론과 같은
 * 길이 이상의 콜론 줄이 닫는다.
 */
function colonFencePlugin(md: MarkdownIt): void {
  md.block.ruler.before(
    'fence',
    'colon_fence',
    (state, startLine, endLine, silent) => {
      const begin = state.bMarks[startLine] as number
      const shift = state.tShift[startLine] as number
      const end = state.eMarks[startLine] as number
      const line = state.src.slice(begin + shift, end)
      const opening = /^(:{3,})[ \t]*([a-zA-Z][a-zA-Z0-9_-]*)?/.exec(line)
      if (!opening) return false
      const marker = opening[1] as string

      let next = startLine + 1
      for (; next < endLine; next += 1) {
        const from = (state.bMarks[next] as number) + (state.tShift[next] as number)
        const to = state.eMarks[next] as number
        const text = state.src.slice(from, to).trim()
        if (/^:+$/.test(text) && text.length >= marker.length) break
      }
      // 닫는 줄이 없으면 컨테이너가 아니다. 문서 나머지를 삼키면 안 된다.
      if (next >= endLine) return false
      if (silent) return true

      const token = state.push('colon_fence', 'div', 0)
      token.info = line.slice(marker.length).trim()
      token.content = state.getLines(startLine + 1, next, state.blkIndent, true)
      token.markup = marker
      token.map = [startLine, next + 1]
      state.line = next + 1
      return true
    },
    { alt: ['paragraph', 'blockquote', 'list'] },
  )

  md.renderer.rules['colon_fence'] = (tokens, idx) => {
    const token = tokens[idx]
    const name = (token?.info ?? '').split('{', 1)[0]?.trim().toLowerCase() ?? ''
    // 이름은 클래스가 된다. 아는 것만 쓴다 — 사용자가 쓴 문자열을 속성에
    // 흘려 넣으면 따옴표 하나로 태그를 빠져나간다.
    const tone = (CONTAINER_NAMES as readonly string[]).includes(name) ? ` ieum-admonition-${name}` : ''
    return `<div class="ieum-admonition${tone}">${md.render(token?.content ?? '')}</div>\n`
  }
}

/**
 * 제목에 `id` 를 붙인다. `::toc` 가 여기로 링크한다.
 *
 * 같은 제목이 두 번 나오면 뒤에 번호를 붙인다. 그 세는 값은 **문서 전체에서
 * 하나**여야 해서 `env` 로 받는다 — 문서가 디렉티브 때문에 여러 조각으로
 * 나뉘어 그려지기 때문이다. 조각마다 새로 세면 목차 링크가 어긋난다.
 */
function headingAnchorPlugin(md: MarkdownIt): void {
  const original = md.renderer.rules['heading_open']
  md.renderer.rules['heading_open'] = (tokens, idx, options, env, self) => {
    const inline = tokens[idx + 1]
    const token = tokens[idx]
    if (token && inline?.type === 'inline') {
      const counts = (env as { slugs?: Map<string, number> } | undefined)?.slugs
      token.attrSet('id', nextSlug(inline.content, counts))
    }
    return original
      ? original(tokens, idx, options, env, self)
      : self.renderToken(tokens, idx, options)
  }
}

/** `collectHeadings` 와 같은 규칙으로 센다. 어긋나면 목차가 헛다리를 짚는다. */
function nextSlug(text: string, counts: Map<string, number> | undefined): string {
  const base = headingSlug(text)
  if (!counts) return base
  const seen = counts.get(base) ?? 0
  counts.set(base, seen + 1)
  return seen === 0 ? base : `${base}-${String(seen + 1)}`
}

let cached: MarkdownIt | null = null

/** 방언대로 설정한 파서. 상태가 없으므로 하나를 공유한다. */
export function parser(): MarkdownIt {
  if (cached) return cached
  const md = new MarkdownIt(PRESET, { ...OPTIONS })
  for (const rule of CORE_RULES) md.enable(rule)
  md.use(frontMatterPlugin)
  md.use(taskLists)
  md.use(footnote)
  md.use(colonFencePlugin)
  md.use(mentionPlugin)
  md.use(headingAnchorPlugin)
  md.validateLink = validateLink
  cached = md
  return md
}

/**
 * 마크다운 → HTML.
 *
 * `slugs` 를 넘기면 제목 `id` 의 중복 번호를 그 맵으로 센다. 한 문서를 여러
 * 번 나눠 그릴 때 같은 맵을 넘겨야 목차 링크가 맞는다.
 */
export function renderMarkdown(text: string, slugs?: Map<string, number>): string {
  return parser().render(text, slugs ? { slugs } : {})
}
