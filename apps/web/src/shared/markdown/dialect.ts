import MarkdownIt from 'markdown-it'
import footnote from 'markdown-it-footnote'
import taskLists from 'markdown-it-task-lists'

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
export const VERSION = 1
export const PRESET = 'commonmark'
export const OPTIONS = {
  html: false,
  linkify: true,
  typographer: false,
  breaks: false,
} as const
export const CORE_RULES = ['table', 'strikethrough'] as const
export const PLUGINS = ['front_matter', 'tasklists', 'footnote'] as const
export const LINK_SCHEMES = ['http', 'https', 'mailto', 'attachment', 'page', 'issue'] as const

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

let cached: MarkdownIt | null = null

/** 방언대로 설정한 파서. 상태가 없으므로 하나를 공유한다. */
export function parser(): MarkdownIt {
  if (cached) return cached
  const md = new MarkdownIt(PRESET, { ...OPTIONS })
  for (const rule of CORE_RULES) md.enable(rule)
  md.use(frontMatterPlugin)
  md.use(taskLists)
  md.use(footnote)
  md.validateLink = validateLink
  cached = md
  return md
}

export function renderMarkdown(text: string): string {
  return parser().render(text)
}
