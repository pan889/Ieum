/**
 * 화면이 단축키를 등록하는 자리. 판정은 `keys.ts` 가 한다.
 *
 * **문서에 리스너를 하나만 단다.** 훅마다 각자 달면 순서가 등록 순서에
 * 끌려다니고, "위에 떠 있는 것이 먼저" 를 만들 수 없다. 여기서는 스코프를
 * 쌓아 두고 하나의 리스너가 **위에서부터** 훑는다.
 */

import { useEffect, useRef, useSyncExternalStore } from 'react'

import { bindingsFrom, type Shortcut, type ShortcutGroup, type ShortcutId } from './catalog'
import { resolve, type Bindings, type Scope } from './keys'

const scopes: Scope[] = []
let listening = false

function onKeyDown(event: KeyboardEvent): void {
  const handler = resolve(scopes, event)
  if (handler === null) return
  // 우리가 처리했으면 브라우저 기본 동작을 막는다. `/` 가 파이어폭스의 빠른
  // 찾기를 열거나, `mod+k` 가 주소창으로 가는 것을 여기서 끊는다.
  event.preventDefault()
  handler(event)
}

function attach(scope: Scope): () => void {
  scopes.push(scope)
  if (!listening) {
    document.addEventListener('keydown', onKeyDown)
    listening = true
  }
  return () => {
    const at = scopes.indexOf(scope)
    if (at !== -1) scopes.splice(at, 1)
    if (scopes.length === 0 && listening) {
      document.removeEventListener('keydown', onKeyDown)
      listening = false
    }
  }
}

export interface HotkeyOptions {
  /** 입력 칸 안에서도 살아 있어야 하는 조합. */
  whileTyping?: readonly string[]
  /** 꺼 두면 등록하지 않는다. 조건부 화면에서 쓴다. */
  enabled?: boolean
}

/**
 * 단축키 한 벌을 이 컴포넌트가 살아 있는 동안 등록한다.
 *
 * `bindings` 는 **매 렌더 새 객체여도 된다.** ref 로 최신 것을 들고 있으므로
 * 등록을 다시 하지 않는다 — 그러지 않으면 부모가 리렌더될 때마다 스코프가
 * 빠졌다 들어가며 순서가 뒤집힌다.
 */
export function useHotkeys(bindings: Bindings, options: HotkeyOptions = {}): void {
  const { enabled = true } = options
  const latest = useRef(bindings)
  // 렌더 중에 ref 를 쓰지 않는다. 렌더마다 도는 effect 로 갱신하면 다음
  // 키 입력이 최신 것을 본다 — 사람이 키를 누르는 시점은 항상 커밋 뒤다.
  useEffect(() => {
    latest.current = bindings
  })

  const typing = options.whileTyping
  const typingKey = (typing ?? []).join(',')

  useEffect(() => {
    if (!enabled) return undefined
    const scope: Scope = {
      // 등록 시점의 `bindings` 를 가두지 않는다.
      get bindings() {
        return latest.current
      },
      whileTyping: typingKey === '' ? [] : typingKey.split(','),
    }
    return attach(scope)
  }, [enabled, typingKey])
}

/**
 * 목록(`catalog`)과 처리 함수를 붙여 등록한다.
 *
 * `bindingsFrom` 을 **렌더 중에 부르지 않는다.** 처리 함수 중에는 ref 를 읽는
 * 것이 있고(검색 칸에 초점 주기), 그것을 렌더 중에 도는 함수에 넘기면
 * `react-hooks/refs` 가 막는다 — 규칙이 옳다. 실제로 부르는 시점은 키를 누른
 * 뒤이므로, 붙이는 일도 effect 안으로 미룬다.
 */
export function useShortcuts<Id extends ShortcutId>(
  shortcuts: readonly Shortcut[],
  handlers: Record<Id, () => void>,
  /** 도움말에 이 묶음을 보여 줄 소제목. 없으면 도움말에 안 뜬다. */
  titleKey?: string,
): void {
  const latest = useRef(handlers)
  useEffect(() => {
    latest.current = handlers
  })

  const combos = shortcuts.map((s) => s.combo).join(',')
  const typing = shortcuts
    .filter((s) => s.whileTyping === true)
    .map((s) => s.combo)
    .join(',')

  useEffect(() => {
    const scope: Scope = {
      get bindings(): Bindings {
        return bindingsFrom(shortcuts, latest.current)
      },
      whileTyping: typing === '' ? [] : typing.split(','),
    }
    const detach = attach(scope)
    if (titleKey === undefined) return detach

    const group: ShortcutGroup = { titleKey, shortcuts }
    groups.push(group)
    republish()
    return () => {
      detach()
      const at = groups.indexOf(group)
      if (at !== -1) groups.splice(at, 1)
      republish()
    }
    // 목록은 상수다. 조합이 그대로면 다시 붙이지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [combos, typing, titleKey])
}

// ── `?` 도움말이 읽는 자리 ────────────────────────────────────────
//
// **도움말은 지금 실제로 살아 있는 것만 보여야 한다.** 목록 화면에서만 도는
// `j`/`k` 를 어디서나 보여 주면, 그것이 이 계층이 이미 한 번 저지른 잘못
// (없는 키를 있는 것처럼 적어 두기)의 화면판이 된다.

const groups: ShortcutGroup[] = []
const watchers = new Set<() => void>()
let snapshot: readonly ShortcutGroup[] = []

function republish(): void {
  snapshot = [...groups]
  for (const notify of watchers) notify()
}

function subscribe(notify: () => void): () => void {
  watchers.add(notify)
  return () => {
    watchers.delete(notify)
  }
}

/** 지금 살아 있는 단축키 묶음들. 등록 순서대로. */
export function useActiveShortcutGroups(): readonly ShortcutGroup[] {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => snapshot,
  )
}
