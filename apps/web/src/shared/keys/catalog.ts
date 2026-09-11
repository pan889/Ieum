/**
 * 전역 단축키 **목록 하나**. 실제 바인딩과 `?` 도움말이 둘 다 여기서 나온다.
 *
 * 두 벌로 두면 한 벌만 갱신되는 날이 오고, 그날 도움말은 **없는 단축키를
 * 안내한다.** 이 저장소는 그 고장을 이미 겪었다 — `ux-principles.md` 가
 * 구현되지 않은 단축키 표를 사실처럼 들고 있었고, 그걸 읽고 사용자 안내에
 * 없는 키를 적을 뻔했다.
 *
 * 여기에 항목을 더하면 `Record<ShortcutId, …>` 때문에 **타입 검사가** 처리
 * 함수를 요구한다. 그래서 "도움말에는 있는데 아무 일도 안 하는 키" 가 생길 수
 * 없다.
 */

import type { Bindings } from './keys'

/**
 * 묶음마다 id 를 따로 둔다. 그래야 각 화면이 **자기 묶음만** 빠짐없이
 * 채우게 강제할 수 있다 — 하나로 합치면 앱셸이 목록 단축키까지 들고 있어야
 * 한다.
 */
export type GlobalShortcutId = 'palette' | 'search' | 'createIssue' | 'help'
export type ListShortcutId = 'listDown' | 'listUp' | 'listOpen'
export type ShortcutId = GlobalShortcutId | ListShortcutId

export interface Shortcut {
  id: ShortcutId
  /** `keys.ts` 의 조합 문자열. */
  combo: string
  /** `common` 네임스페이스의 번역 키. */
  labelKey: string
  /** 입력 칸 안에서도 살아 있어야 하는가. */
  whileTyping?: boolean
}

/** 한 화면이 내놓는 단축키 묶음. `?` 도움말이 이 단위로 보여 준다. */
export interface ShortcutGroup {
  /** `common` 의 번역 키. 도움말의 소제목. */
  titleKey: string
  shortcuts: readonly Shortcut[]
}

export const GLOBAL_SHORTCUTS: readonly Shortcut[] = [
  // 검색어를 치다가도 열 수 있어야 한다 — 그래서 이것만 입력 칸을 뚫는다.
  { id: 'palette', combo: 'mod+k', labelKey: 'keys.palette', whileTyping: true },
  { id: 'search', combo: '/', labelKey: 'keys.search' },
  { id: 'createIssue', combo: 'c', labelKey: 'keys.createIssue' },
  { id: 'help', combo: '?', labelKey: 'keys.help' },
]

/**
 * 목록 화면. `o` 와 `Enter` 는 같은 일을 한다 — `o` 는 손이 익은 사람용이고
 * `Enter` 는 아무 설명 없이도 맞는 쪽이다.
 */
export const LIST_SHORTCUTS: readonly Shortcut[] = [
  { id: 'listDown', combo: 'j', labelKey: 'keys.listDown' },
  { id: 'listUp', combo: 'k', labelKey: 'keys.listUp' },
  { id: 'listOpen', combo: 'o', labelKey: 'keys.listOpen' },
  { id: 'listOpen', combo: 'enter', labelKey: 'keys.listOpen' },
]

/** 목록과 처리 함수를 맞붙인다. 빠진 것이 있으면 타입이 막는다. */
export function bindingsFrom<Id extends ShortcutId>(
  shortcuts: readonly Shortcut[],
  handlers: Record<Id, () => void>,
): Bindings {
  const bindings: Bindings = {}
  for (const shortcut of shortcuts) {
    // 목록에는 이 묶음이 안 맡는 id 가 섞여 올 수 있다(다른 묶음의 것).
    // 그래서 조회 결과를 **없을 수도 있는 것**으로 본다.
    const lookup: Partial<Record<ShortcutId, () => void>> = handlers
    const handler = lookup[shortcut.id]
    if (handler === undefined) continue
    bindings[shortcut.combo] = handler
  }
  return bindings
}

/** 입력 칸을 뚫어야 하는 조합만. */
export function whileTypingCombos(shortcuts: readonly Shortcut[]): string[] {
  return shortcuts.filter((s) => s.whileTyping === true).map((s) => s.combo)
}

/**
 * 사람에게 보여 줄 모양. 맥은 `⌘K`, 나머지는 `Ctrl+K`.
 *
 * 맥에서 `Ctrl+K` 라고 적어 두면 사람이 그것을 눌러 보고 안 된다고 여긴다 —
 * 실제로는 되는데 안내가 틀린 것이다.
 */
export function formatCombo(combo: string, mac: boolean): string {
  return combo
    .split('+')
    .map((part) => {
      if (part === 'mod') return mac ? '⌘' : 'Ctrl'
      if (part === 'alt') return mac ? '⌥' : 'Alt'
      if (part === 'escape') return 'Esc'
      if (part === 'enter') return 'Enter'
      if (part.length === 1) return part.toUpperCase()
      return part
    })
    .join(mac ? '' : '+')
}

/** 이 브라우저가 맥인가. `?` 도움말과 팔레트가 같은 답을 쓰게 한 곳에 둔다. */
export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false
  // `navigator.platform` 은 폐기됐다. userAgent 로 본다 — 틀려도 손해는
  // 표기 하나(`⌘` 대신 `Ctrl`)이고, 키는 양쪽 다 받는다.
  return /mac|iphone|ipad/i.test(navigator.userAgent)
}
