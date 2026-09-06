/**
 * `@` 자동완성의 순수 부분.
 *
 * 커서 앞에서 진행 중인 멘션을 찾고, 고른 사용자를 본문에 끼워 넣는다.
 * DOM 없이 테스트할 수 있게 문자열 조작만 여기 둔다.
 */

export interface MentionQuery {
  /** `@` 의 위치. */
  start: number
  /** `@` 뒤에 입력된 글자. 빈 문자열이면 방금 `@` 를 친 것이다. */
  term: string
}

/** 이름에 들어갈 수 있는 글자. 공백 두 개까지는 이름 일부로 본다. */
const TERM = /^[^\s@]*(?:\s[^\s@]*){0,2}$/

/**
 * 커서 바로 앞에서 진행 중인 멘션을 찾는다. 없으면 null.
 *
 * `@` 앞이 글자면 멘션이 아니다 — 이메일(`a@b.com`)을 멘션으로 읽으면
 * 주소를 칠 때마다 목록이 뜬다.
 */
export function findMentionQuery(text: string, caret: number): MentionQuery | null {
  const before = text.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1) return null

  const preceding = at === 0 ? '' : (before[at - 1] as string)
  if (preceding !== '' && !/[\s([]/.test(preceding)) return null

  const term = before.slice(at + 1)
  if (!TERM.test(term)) return null
  return { start: at, term }
}

/** 고른 사용자를 마크다운 멘션으로 바꿔 끼운다. 새 커서 위치도 돌려준다. */
export function applyMention(
  text: string,
  query: MentionQuery,
  caret: number,
  user: { id: string; display_name: string },
): { text: string; caret: number } {
  const snippet = `[@${user.display_name}](user:${user.id})`
  const next = text.slice(0, query.start) + snippet + text.slice(caret)
  return { text: next, caret: query.start + snippet.length }
}
