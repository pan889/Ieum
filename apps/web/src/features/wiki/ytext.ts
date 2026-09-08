/**
 * `Y.Text` 와 `<textarea>` 사이의 순수 계산 (B16).
 *
 * 여기 있는 두 함수가 동시 편집에서 사람이 **몸으로 느끼는** 부분이다:
 *
 * - `singleEdit`: textarea 는 "값이 이렇게 바뀌었다" 만 알려 준다. CRDT 는
 *   "어디를 지우고 무엇을 넣었다" 를 원한다. 그 변환을 잘못하면 남의 편집이
 *   덮인다 — 전체를 지우고 새로 넣는 식으로 옮기면 매 타이핑이 문서 전체를
 *   다시 쓰는 편집이 되고, 같은 순간 옆 사람이 친 글자가 사라진다.
 * - `shiftCaret`: 원격 편집이 들어오면 내 캐럿이 움직여야 한다. 안 움직이면
 *   위에서 남이 한 줄 쓸 때마다 내 커서가 뒤로 밀린다 — 같이 쓰는 자리에서
 *   가장 빨리 포기하게 되는 이유다.
 *
 * 둘 다 순수 함수로 빼 둔 이유: 이 계산이 틀리면 브라우저에서는 "가끔 글자가
 * 튄다" 로만 보인다. 그 증상으로는 원인을 못 찾는다.
 */

export interface Edit {
  /** 몇 번째 글자부터. */
  at: number
  /** 몇 글자를 지우는가. */
  remove: number
  /** 무엇을 넣는가. */
  insert: string
}

/**
 * 두 문자열의 차이를 **한 번의 편집**으로 줄인다.
 *
 * 앞뒤로 같은 부분을 벗겨 내고 남은 가운데만 바꾼다. 사람의 타이핑은 거의
 * 항상 한 자리에서 일어나므로 이 모양이면 충분하고, 붙여넣기·잘라내기도
 * 한 구간이다.
 *
 * 바뀐 것이 없으면 `null`. 그냥 빈 편집을 돌려주면 부르는 쪽이 CRDT 에
 * 아무 일도 안 하는 트랜잭션을 열고, 그것이 다른 사람에게 방송된다.
 */
export function singleEdit(before: string, after: string): Edit | null {
  if (before === after) return null

  let prefix = 0
  const shortest = Math.min(before.length, after.length)
  while (prefix < shortest && before[prefix] === after[prefix]) prefix += 1

  let suffix = 0
  while (
    suffix < shortest - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) {
    suffix += 1
  }

  return {
    at: prefix,
    remove: before.length - prefix - suffix,
    insert: after.slice(prefix, after.length - suffix),
  }
}

/**
 * 원격 편집이 들어온 뒤 캐럿이 있어야 할 자리.
 *
 * 규칙은 세 가지다:
 *
 * - 편집이 **내 뒤에서** 일어났으면 나는 그대로다.
 * - 편집이 **내 앞에서** 끝났으면 그만큼 밀린다.
 * - 편집 구간이 **나를 물고 있으면** 그 구간의 시작으로 간다. 지워진 글자
 *   가운데를 가리키고 있을 수는 없다.
 */
export function shiftCaret(caret: number, edit: Edit): number {
  if (edit.at >= caret) return caret
  const end = edit.at + edit.remove
  if (end <= caret) return caret - edit.remove + edit.insert.length
  return edit.at + edit.insert.length
}

/** 선택 구간 양 끝을 함께 옮긴다. */
export function shiftRange(
  range: { start: number; end: number },
  edit: Edit,
): { start: number; end: number } {
  return { start: shiftCaret(range.start, edit), end: shiftCaret(range.end, edit) }
}
