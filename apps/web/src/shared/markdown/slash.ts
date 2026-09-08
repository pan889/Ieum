/**
 * `/` 삽입 명령의 순수 부분 (ux-principles 6절).
 *
 * 표·코드·체크박스는 마크다운 단축으로 칠 수 있지만, 디렉티브는 아니다 —
 * `::children{depth=2}` 를 외우고 있는 사람은 없다. 무엇이 있는지 목록으로
 * 보여 주고 문법은 우리가 적는다.
 *
 * **후보는 디렉티브 정의에서 끌어온다.** 손으로 나열하면 새 디렉티브가
 * 생겨도 목록에는 안 뜨고, 없어진 디렉티브가 목록에 남는다. 이름이 한 곳에
 * 있어야 갈라지지 않는다 (`directives.ts`).
 *
 * DOM 없이 테스트할 수 있게 문자열 조작만 여기 둔다. 서식 모드도 같은
 * 목록·같은 마크다운을 쓴다 — 모드에 따라 넣어지는 것이 다르면 안 된다.
 */

import { CONTAINER_NAMES, LEAF_NAMES } from './directives'

export interface SlashItem {
  /** 번역 키(`common:slash.<id>`)이자 목록의 식별자. */
  id: string
  /** 끼워 넣을 마크다운. */
  insert: string
  /** `insert` 안에서 커서가 멈출 자리. 사람이 이어 쓸 곳이다. */
  caret: number
  /**
   * 이름 말고도 걸릴 낱말.
   *
   * 화면 글자가 아니라 **검색어**다. 그래서 번역하지 않는다 — 한국어 화면인
   * 사람이 `table` 로 찾고, 영어 화면인 사람이 `표` 로 찾을 수 있어야 한다.
   */
  keywords: string[]
}

/**
 * 커서 자리 표시.
 *
 * `|` 를 쓸 뻔했다가 표 조각에서 걸렸다 — `| --- |` 의 첫 칸막이가 커서로
 * 읽혀 표가 깨진다. 사람이 칠 수 없는 글자여야 조각과 자리가 안 어긋난다.
 */
const CARET = '\u0001'

function item(id: string, template: string, keywords: string[] = []): SlashItem {
  const caret = template.indexOf(CARET)
  if (caret === -1) throw new Error(`${id}: 커서 자리를 표시하지 않았다`)
  return { id, insert: template.replace(CARET, ''), caret, keywords: [id, ...keywords] }
}

/** 편집기가 모델링하는 블록. 마크다운 단축과 같은 것을 만든다. */
const BLOCKS: SlashItem[] = [
  item('heading1', `# ${CARET}`, ['h1', '제목']),
  item('heading2', `## ${CARET}`, ['h2', '제목']),
  item('heading3', `### ${CARET}`, ['h3', '제목']),
  item('bulletList', `- ${CARET}`, ['ul', '목록']),
  item('orderedList', `1. ${CARET}`, ['ol', '번호', '목록']),
  item('taskList', `- [ ] ${CARET}`, ['todo', '체크박스', '할일']), // i18n-exempt: 검색어
  item('quote', `> ${CARET}`, ['blockquote', '인용']),
  item('code', `\`\`\`\n${CARET}\n\`\`\``, ['fence', '코드']),
  item('table', `| ${CARET} |  |\n| --- | --- |\n|  |  |`, ['표']),
  item('divider', `---\n\n${CARET}`, ['hr', '구분선']),
]

/**
 * **없으면 저장이 안 되는 인자는 조각이 들고 온다.**
 *
 * `::issues`·`::excerpt`·`::chart` 는 인자 없이는 저장이 거절된다
 * (`directives.py`). 이름만 끼워 넣으면 사람은 목록에서 고른 것을 그대로
 * 저장했다가 거절을 받는다 — 무엇을 더 적어야 하는지는 화면에 없다. 그래서
 * 틀을 넣고 **커서를 채울 자리에 놓는다.**
 *
 * 여기 없는 이름은 인자 없이도 뜻이 되는 것들이다(`::toc`, `::children`).
 */
const LEAF_TEMPLATES: Record<string, string> = {
  issues: `::issues{query="${CARET}"}`,
  excerpt: `::excerpt{page="${CARET}"}`,
  chart: `::chart{query="${CARET}" group=status}`,
}

/** 디렉티브. 이름은 `directives.ts` 가 들고 있다 — 여기서 다시 적지 않는다. */
function directiveItems(): SlashItem[] {
  const leaves = LEAF_NAMES.map((name) =>
    // 인자가 필요 없는 리프는 커서를 뒤에 놓는다 — 이어 쓸 자리는 다음 줄이다.
    item(name, LEAF_TEMPLATES[name] ?? `::${name}\n\n${CARET}`, ['directive', '디렉티브']), // i18n-exempt: 검색어
  )
  const containers = CONTAINER_NAMES.map((name) =>
    item(name, `:::${name}\n${CARET}\n:::`, ['directive', 'callout', '디렉티브', '상자']), // i18n-exempt: 검색어
  )
  return [...leaves, ...containers]
}

/** 넣을 수 있는 것 전부. 순서가 곧 목록 순서다. */
export function slashItems(): SlashItem[] {
  return [...BLOCKS, ...directiveItems()]
}

export interface SlashQuery {
  /** `/` 의 위치. */
  start: number
  /** `/` 뒤에 입력된 글자. */
  term: string
}

/** 이름에 들어갈 수 있는 글자. 공백이 오면 명령이 아니라 그냥 글이다. */
const TERM = /^[A-Za-z0-9가-힣_-]*$/

/**
 * 커서 바로 앞에서 진행 중인 `/` 명령을 찾는다. 없으면 null.
 *
 * **줄 맨 앞의 `/` 만** 명령이다. 아무 데서나 열면 경로(`src/shared`)나
 * 날짜(`9/6`)를 칠 때마다 목록이 튀어나온다.
 */
export function findSlashQuery(text: string, caret: number): SlashQuery | null {
  const before = text.slice(0, caret)
  const slash = before.lastIndexOf('/')
  if (slash === -1) return null
  const lineStart = before.lastIndexOf('\n') + 1
  if (slash !== lineStart) return null

  const term = before.slice(slash + 1)
  if (!TERM.test(term)) return null
  return { start: slash, term }
}

/**
 * 쳐 넣은 글자로 목록을 좁힌다.
 *
 * 앞에서부터 맞는 것을 먼저 준다. `tab` 을 쳤을 때 `table` 이 `taskList`
 * 보다 앞에 와야 한다 — 사람은 목록을 읽지 않고 첫 줄에서 엔터를 친다.
 */
export function matchSlash(items: SlashItem[], term: string): SlashItem[] {
  const needle = term.toLowerCase()
  if (needle === '') return items
  const scored: { item: SlashItem; rank: number }[] = []
  for (const entry of items) {
    const hit = entry.keywords
      .map((word) => word.toLowerCase().indexOf(needle))
      .filter((at) => at !== -1)
    if (hit.length === 0) continue
    scored.push({ item: entry, rank: Math.min(...hit) })
  }
  return scored.sort((a, b) => a.rank - b.rank).map((entry) => entry.item)
}

/**
 * 고른 것을 본문에 끼운다. 새 커서 위치도 돌려준다.
 *
 * `/` 앞에 글이 있었으면 블록을 시작할 수 없다. 빈 줄을 하나 넣어 준다 —
 * 안 그러면 `## 제목` 이 앞 문단에 이어 붙어 제목이 되지 않는다.
 */
export function applySlash(
  text: string,
  query: SlashQuery,
  caret: number,
  chosen: SlashItem,
): { text: string; caret: number } {
  const head = text.slice(0, query.start)
  const gap = head === '' || head.endsWith('\n\n') ? '' : head.endsWith('\n') ? '\n' : '\n\n'
  const next = head + gap + chosen.insert + text.slice(caret)
  return { text: next, caret: query.start + gap.length + chosen.caret }
}
