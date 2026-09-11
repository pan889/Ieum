/**
 * 단축키 판정. **여기가 이 기능의 위험한 자리다.**
 *
 * 단축키가 틀리는 방식은 둘인데 무게가 다르다. 안 먹는 것은 사람이 곧 알고
 * 마우스를 쓴다. **글 쓰는 중에 튀는 것**은 쓰던 것을 잃고, 왜 그랬는지도
 * 모른다 — 코멘트에 "create" 를 치다가 `c` 로 새 이슈 화면에 끌려가는 식이다.
 * 그래서 이 파일의 절반이 "안 먹어야 하는 자리" 다.
 */

import { describe, expect, it, vi } from 'vitest'

import { comboOf, isTypingTarget, resolve, type Scope } from './keys'

function press(key: string, init: Partial<KeyboardEventInit> = {}): KeyboardEvent {
  return new KeyboardEvent('keydown', { key, ...init })
}

/** 이벤트에 `target` 을 붙인다. jsdom 은 dispatch 없이는 안 채워 준다. */
function at(event: KeyboardEvent, target: EventTarget): KeyboardEvent {
  Object.defineProperty(event, 'target', { value: target, configurable: true })
  return event
}

describe('comboOf', () => {
  it('맨 글자는 소문자로 준다', () => {
    expect(comboOf(press('c'))).toBe('c')
    expect(comboOf(press('J'))).toBe('j')
  })

  it('Cmd 와 Ctrl 을 mod 하나로 합친다', () => {
    expect(comboOf(press('k', { metaKey: true }))).toBe('mod+k')
    expect(comboOf(press('k', { ctrlKey: true }))).toBe('mod+k')
  })

  it('물음표는 그대로 온다 — Shift 를 따로 세지 않는다', () => {
    expect(comboOf(press('?', { shiftKey: true }))).toBe('?')
  })

  it('이름 있는 키도 소문자다', () => {
    expect(comboOf(press('Escape'))).toBe('escape')
    expect(comboOf(press('Enter'))).toBe('enter')
  })
})

describe('isTypingTarget', () => {
  it('글을 받는 자리를 알아본다', () => {
    const text = document.createElement('input')
    text.type = 'text'
    expect(isTypingTarget(text)).toBe(true)
    expect(isTypingTarget(document.createElement('textarea'))).toBe(true)
    expect(isTypingTarget(document.createElement('select'))).toBe(true)
  })

  it('검색 칸도 글을 받는 자리다', () => {
    const search = document.createElement('input')
    search.type = 'search'
    expect(isTypingTarget(search)).toBe(true)
  })

  it('서식 편집기(contenteditable)를 놓치지 않는다', () => {
    const editor = document.createElement('div')
    editor.contentEditable = 'true'
    // jsdom 은 `isContentEditable` 을 계산해 주지 않는다.
    Object.defineProperty(editor, 'isContentEditable', { value: true })
    expect(isTypingTarget(editor)).toBe(true)
  })

  it('**글을 안 받는 input 은 아니다** — 체크박스에 초점이 있다고 키보드가 먹통이 되면 안 된다', () => {
    for (const kind of ['checkbox', 'radio', 'button', 'submit', 'file']) {
      const el = document.createElement('input')
      el.type = kind
      expect(isTypingTarget(el), kind).toBe(false)
    }
  })

  it('보통 요소는 아니다', () => {
    expect(isTypingTarget(document.createElement('div'))).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})

describe('resolve', () => {
  const body = document.createElement('div')

  it('등록된 것을 찾아 준다', () => {
    const hit = vi.fn()
    const scopes: Scope[] = [{ bindings: { c: hit } }]
    expect(resolve(scopes, at(press('c'), body))).toBe(hit)
  })

  it('없는 키는 null', () => {
    const scopes: Scope[] = [{ bindings: { c: vi.fn() } }]
    expect(resolve(scopes, at(press('x'), body))).toBeNull()
  })

  it('**글 쓰는 중에는 안 먹는다**', () => {
    const scopes: Scope[] = [{ bindings: { c: vi.fn() } }]
    const box = document.createElement('textarea')
    expect(resolve(scopes, at(press('c'), box))).toBeNull()
  })

  it('whileTyping 에 넣은 것은 글 쓰는 중에도 먹는다', () => {
    const hit = vi.fn()
    const scopes: Scope[] = [{ bindings: { 'mod+k': hit }, whileTyping: ['mod+k'] }]
    const box = document.createElement('textarea')
    expect(resolve(scopes, at(press('k', { metaKey: true }), box))).toBe(hit)
  })

  it('위에 떠 있는 스코프가 이긴다', () => {
    const under = vi.fn()
    const over = vi.fn()
    const scopes: Scope[] = [{ bindings: { enter: under } }, { bindings: { enter: over } }]
    expect(resolve(scopes, at(press('Enter'), body))).toBe(over)
  })

  it('위 스코프에 없으면 아래로 내려간다', () => {
    const under = vi.fn()
    const scopes: Scope[] = [{ bindings: { c: under } }, { bindings: { escape: vi.fn() } }]
    expect(resolve(scopes, at(press('c'), body))).toBe(under)
  })

  it('글 쓰는 중이면 위 스코프에 없는 키도 안 먹는다', () => {
    const global = vi.fn()
    const scopes: Scope[] = [{ bindings: { c: global } }, { bindings: { escape: vi.fn() } }]
    const box = document.createElement('input')
    box.type = 'text'
    expect(resolve(scopes, at(press('c'), box))).toBeNull()
  })

  it('**키를 잡은 제일 위 스코프가 그 키를 소유한다** — 아래로 새지 않는다', () => {
    // 위 스코프가 `c` 를 잡았고 글 쓰는 중이라 죽는다. 그렇다고 해서 아래의
    // `c`(글 쓰는 중에도 살아 있다고 선언한 것)가 대신 튀면 안 된다 —
    // 그러면 화면에 떠 있는 것이 삼킨 키가 뒤에서 다른 일을 한다.
    //
    // 이 시험이 없으면 `return null` 을 `continue` 로 바꿔도 아무도 안 붉어진다.
    // 실제로 처음엔 그랬고, 되돌려 보고서야 알았다.
    const below = vi.fn()
    const scopes: Scope[] = [
      { bindings: { c: below }, whileTyping: ['c'] },
      { bindings: { c: vi.fn() } },
    ]
    const box = document.createElement('input')
    box.type = 'text'
    expect(resolve(scopes, at(press('c'), box))).toBeNull()
  })

  it('IME 조합 중에는 손대지 않는다', () => {
    const scopes: Scope[] = [{ bindings: { c: vi.fn() } }]
    const composing = press('c')
    Object.defineProperty(composing, 'isComposing', { value: true })
    expect(resolve(scopes, at(composing, body))).toBeNull()
  })

  it('누르고 있는 것(repeat)은 막지 않는다 — 목록을 눌러 두고 내려가야 한다', () => {
    const hit = vi.fn()
    const scopes: Scope[] = [{ bindings: { j: hit } }]
    expect(resolve(scopes, at(press('j', { repeat: true }), body))).toBe(hit)
  })
})
