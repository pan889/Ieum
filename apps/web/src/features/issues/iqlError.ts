/**
 * IQL 오류를 어디가 틀렸는지 보이게 만드는 순수 부분.
 *
 * 서버는 offset/length 를 반드시 준다(query-language.md 4절). "질의 어딘가가
 * 틀렸다" 로 끝내면 그 정보가 버려진다 — 스무 자짜리 질의라면 몰라도, 조건
 * 다섯 개짜리 질의에서는 어디를 고쳐야 할지 알 수 없다.
 */

export interface ErrorSpan {
  offset: number
  length: number
}

export interface Excerpt {
  /** 틀린 자리가 있는 줄. 너무 길면 앞뒤를 잘라 낸다. */
  line: string
  /** 그 아래에 깔 캐럿 줄. 같은 폭의 글꼴을 전제한다. */
  caret: string
}

/** 잘라 낸 자리 표시. 한 글자라 캐럿 정렬이 어긋나지 않는다. */
const ELLIPSIS = '…'

/**
 * 틀린 자리를 캐럿으로 가리키는 두 줄.
 *
 * 질의는 여러 줄일 수 있으므로 해당 줄만 뽑고, 그 줄이 화면보다 길면 틀린
 * 자리를 가운데 두고 창을 민다. 캐럿이 화면 밖에 있으면 없느니만 못하다.
 */
export function excerpt(text: string, span: ErrorSpan, width = 56): Excerpt {
  const offset = Math.max(0, Math.min(span.offset, text.length))
  const length = Math.max(1, span.length)

  const lineStart = text.lastIndexOf('\n', offset - 1) + 1
  const lineEnd = text.indexOf('\n', offset) === -1 ? text.length : text.indexOf('\n', offset)
  const line = text.slice(lineStart, lineEnd)
  const column = offset - lineStart

  if (line.length <= width) {
    return { line, caret: ' '.repeat(column) + '^'.repeat(Math.min(length, line.length - column || 1)) }
  }

  // 틀린 자리를 가운데 둔 창.
  const half = Math.floor((width - length) / 2)
  const from = Math.max(0, Math.min(column - half, line.length - width))
  const to = Math.min(line.length, from + width)

  const head = from > 0 ? ELLIPSIS : ''
  const tail = to < line.length ? ELLIPSIS : ''
  return {
    line: head + line.slice(from, to) + tail,
    caret: ' '.repeat(head.length + column - from) + '^'.repeat(Math.min(length, to - column || 1)),
  }
}

/** 제안한 이름으로 틀린 자리를 갈아 끼운다. 새 커서 위치도 돌려준다. */
export function applySuggestion(
  text: string,
  span: ErrorSpan,
  replacement: string,
): { text: string; caret: number } {
  const offset = Math.max(0, Math.min(span.offset, text.length))
  const end = Math.min(text.length, offset + Math.max(0, span.length))
  return {
    text: text.slice(0, offset) + replacement + text.slice(end),
    caret: offset + replacement.length,
  }
}
