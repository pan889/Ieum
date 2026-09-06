/**
 * IQL 자동완성의 순수 부분.
 *
 * 무엇을 제안할지는 **서버가 정한다** — 문법이 서버에 있기 때문이다
 * (D-77 과 같은 이유로 클라이언트에 파서를 또 만들지 않는다). 여기 있는 것은
 * 고른 후보를 본문에 끼워 넣고 커서를 옮기는 일뿐이라 DOM 없이 검증된다.
 */

import type { IqlSuggestion, IqlSuggestions } from '@ieum/api-client'

export interface Applied {
  text: string
  caret: number
}

/** 고른 후보를 끼워 넣는다. 서버가 준 범위를 그대로 갈아 끼운다. */
export function applySuggestion(
  text: string,
  range: Pick<IqlSuggestions, 'offset' | 'length'>,
  item: IqlSuggestion,
): Applied {
  const head = text.slice(0, range.offset)
  const tail = text.slice(range.offset + range.length)
  return { text: head + item.insert + tail, caret: range.offset + item.caret }
}

/**
 * 목록 안에서 한 칸 움직인다. 끝에서 넘어가면 반대편으로 돈다.
 *
 * 감싸 돌지 않으면 마지막 항목에서 아래 화살표가 먹통이 된다 — 눌리는데
 * 아무 일도 안 나는 키는 고장으로 읽힌다.
 */
export function moveActive(active: number, delta: number, count: number): number {
  if (count === 0) return 0
  return (active + delta + count) % count
}

/**
 * 커서가 바뀌었는지. 값이 같으면 다시 묻지 않는다.
 *
 * textarea 는 커서만 움직여도 selectionchange 를 흘리므로, 위치가 그대로인
 * 이벤트까지 요청으로 바꾸면 타이핑 한 번에 요청이 여러 번 나간다.
 */
export function samePlace(
  a: { iql: string; offset: number } | null,
  b: { iql: string; offset: number } | null,
): boolean {
  if (a === null || b === null) return a === b
  return a.iql === b.iql && a.offset === b.offset
}
