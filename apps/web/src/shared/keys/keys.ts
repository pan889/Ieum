/**
 * 전역 단축키의 **판정부**. React 도 DOM 등록도 없는 순수 함수만 둔다.
 *
 * 여기를 따로 두는 이유는 하나다: 단축키에서 사람을 제일 화나게 하는 고장이
 * **글을 쓰는 중에 단축키가 튀는 것**이고(코멘트에 "create" 를 치다가 `c` 로
 * 새 이슈 화면으로 끌려간다), 그 판정은 브라우저 없이 시험할 수 있어야 한다.
 * `useHotkeys` 는 이 함수들을 부르기만 한다.
 */

/** 한 벌의 단축키. 키 조합 문자열 → 할 일. */
export type Bindings = Record<string, (event: KeyboardEvent) => void>

export interface Scope {
  bindings: Bindings
  /**
   * 입력 칸 안에서도 살아 있어야 하는 조합. 기본은 전부 죽는다.
   *
   * `mod+k` 는 여기 들어간다 — 검색어를 치다가도 팔레트를 열 수 있어야 하고,
   * 브라우저의 기본 동작과 겹치지 않는다.
   */
  whileTyping?: readonly string[]
  /**
   * 떠 있는 동안 **아래를 통째로 덮는다.** 자기가 모르는 조합은 아무 일도
   * 하지 않는다.
   *
   * 팔레트와 `?` 도움말이 이것이다. 없으면 도움말을 띄워 둔 채 `c` 를 눌러
   * 새 이슈 화면으로 끌려가고, **덮개는 그 위에 그대로 남는다** — 사람이
   * 보기에는 화면이 안 바뀌었는데 주소만 달라진 상태다.
   */
  modal?: boolean
}

/**
 * 아무 일도 안 하지만 **브라우저 기본 동작으로 새어 나가지는 않게** 붙잡는다.
 * 처리한 조합은 `useHotkeys` 가 `preventDefault` 해 주기 때문이다.
 *
 * 덮개가 떠 있는 동안의 `mod+k` 가 이것이다 — 이미 열려 있으니 다시 열 일이
 * 없는데, 그냥 두면 크롬에서 **주소창이 열린다.**
 */
export const swallow = (): void => undefined

/**
 * 이벤트를 조합 문자열로 바꾼다.
 *
 * `Cmd` 와 `Ctrl` 을 **`mod` 하나로 합친다.** 맥과 나머지를 갈라 두면 두 벌을
 * 관리하게 되고, 한 벌만 고쳐지는 날이 온다.
 */
export function comboOf(event: KeyboardEvent): string {
  const key = event.key.toLowerCase()
  if (event.metaKey || event.ctrlKey) return `mod+${key}`
  if (event.altKey) return `alt+${key}`
  return key
}

/** 텍스트를 받는 자리인가. 여기서는 단축키가 죽어야 한다. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false

  // Tiptap 의 서식 편집기가 이 갈래다. `input` 이 아니라서 놓치기 쉽다.
  if (target.isContentEditable) return true

  const tag = target.tagName
  if (tag === 'TEXTAREA') return true
  // 선택 상자는 글자를 눌러 항목을 고른다 — `s` 가 단축키가 되면 그것이 죽는다.
  if (tag === 'SELECT') return true

  if (target instanceof HTMLInputElement) {
    // **모든 `input` 이 글을 받는 것은 아니다.** 체크박스·라디오·버튼에
    // 초점이 있을 때까지 단축키를 죽이면, 목록에서 체크 하나 누른 뒤로는
    // 키보드가 통째로 먹통이 된다.
    const kind = target.type.toLowerCase()
    const notTyping = ['button', 'checkbox', 'radio', 'submit', 'reset', 'file', 'range', 'color']
    return !notTyping.includes(kind)
  }

  return false
}

/**
 * 이 이벤트를 처리할 것을 찾는다. 없으면 `null`.
 *
 * **나중에 올라온 스코프가 먼저다.** 팔레트가 열려 있으면 그 안의 `Enter` 가
 * 목록의 `Enter` 를 이긴다 — 화면에 떠 있는 것이 우선이라는 뜻이고, 사람이
 * 기대하는 순서도 그것이다.
 */
export function resolve(
  scopes: readonly Scope[],
  event: KeyboardEvent,
): ((event: KeyboardEvent) => void) | null {
  // 조합 중인 글자(한글·일본어 IME)는 건드리지 않는다. `ㅇ` 를 치는 중에
  // `keydown` 이 `Process` 로 오고, 그것을 단축키로 읽으면 조합이 깨진다.
  if (event.isComposing) return null

  // **누르고 있는 것(`event.repeat`)은 막지 않는다.** 목록에서 `j` 를 눌러
  // 두고 내려가는 것이 이 계층의 요점 중 하나다. 대신 반복이 해로운 동작은
  // 스스로 멱등해야 한다 — 그래서 `mod+k` 는 토글이 아니라 **열기**다.

  const combo = comboOf(event)
  const typing = isTypingTarget(event.target)

  for (let i = scopes.length - 1; i >= 0; i -= 1) {
    const scope = scopes[i]
    if (scope === undefined) continue
    const handler = scope.bindings[combo]
    if (handler === undefined) {
      // 덮개가 모르는 키는 **아래에도 닿지 않는다.** 화면을 덮고 있다는 것은
      // 뒤에 있는 것을 잠깐 못 쓰게 한다는 뜻이다.
      if (scope.modal === true) return null
      continue
    }
    if (typing && !(scope.whileTyping ?? []).includes(combo)) {
      // **여기서 멈춘다.** 아래 스코프로 안 내려간다 — 글을 쓰는 중이라는
      // 사실은 스코프마다 달라지지 않는다.
      return null
    }
    return handler
  }
  return null
}
