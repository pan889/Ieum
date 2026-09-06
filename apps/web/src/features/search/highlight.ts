/**
 * 검색어 강조.
 *
 * 서버는 HTML 을 만들어 보내지 않는다(PGroonga 의 `pgroonga_snippet_html` 을
 * 쓰지 않는 이유다) — 본문에 HTML 을 흘려보내는 통로를 하나 더 만들면
 * `html: false` 라는 유일한 방어선의 의미가 옅어진다. 대신 낱말 목록을 받아
 * 여기서 쪼갠다.
 */

export interface TextPart {
  text: string
  hit: boolean
}

export function splitByKeywords(text: string, keywords: string[]): TextPart[] {
  const terms = keywords.filter((k) => k.trim().length > 0)
  if (terms.length === 0) return [{ text, hit: false }]

  const pattern = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi')
  const parts: TextPart[] = []
  let at = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index
    if (start > at) parts.push({ text: text.slice(at, start), hit: false })
    parts.push({ text: match[0], hit: true })
    at = start + match[0].length
  }
  if (at < text.length) parts.push({ text: text.slice(at), hit: false })
  return parts
}

/** 검색어는 사용자가 친 글자다. 그대로 정규식에 넣으면 패턴이 깨진다. */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
