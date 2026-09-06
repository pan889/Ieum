/**
 * 서식 모드의 `@` 멘션 — 편집기에 붙는 부분.
 *
 * 소스 모드의 `mention.ts` 와 **같은 결과**를 만든다:
 * `[@이름](user:<uuid>)`. 이름이 바뀌어도 안 깨지는 저장 형태가 하나여야
 * 두 모드가 같은 문서를 쓴다.
 *
 * 목록 UI 는 React 가 그린다. 여기서는 "언제 열고 무엇을 넘길지" 만 정하고
 * 상태를 밖으로 던진다 — tippy 같은 팝업 라이브러리를 하나 더 들이지 않기
 * 위해서다.
 */

import { Extension } from '@tiptap/core'
import Suggestion from '@tiptap/suggestion'
import type { Editor, Range } from '@tiptap/core'

export interface MentionTarget {
  id: string
  display_name: string
}

export interface MentionState {
  /** `@` 뒤에 입력된 글자. 빈 문자열이면 방금 `@` 를 친 것이다. */
  term: string
  /** 화면에서 목록을 놓을 자리. */
  rect: DOMRect | null
  /** 고른 사람을 본문에 끼운다. */
  pick: (user: MentionTarget) => void
}

export interface MentionHooks {
  onChange: (state: MentionState | null) => void
  /** 열려 있을 때의 키 처리. true 를 주면 편집기가 그 키를 안 먹는다. */
  onKeyDown: (event: KeyboardEvent) => boolean
}

/** 고른 사람을 링크 마크가 붙은 글자로 끼운다. */
function insert(editor: Editor, range: Range, user: MentionTarget): void {
  editor
    .chain()
    .focus()
    .insertContentAt(range, [
      {
        type: 'text',
        text: `@${user.display_name}`,
        marks: [{ type: 'link', attrs: { href: `user:${user.id}` } }],
      },
      // 뒤 공백에는 마크를 붙이지 않는다. 붙이면 이어 치는 글자가 전부
      // 멘션 링크 안으로 들어간다.
      { type: 'text', text: ' ' },
    ])
    .run()
}

export function mentionExtension(hooks: MentionHooks): Extension {
  return Extension.create({
    name: 'ieumMention',
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          char: '@',
          // 이메일(`a@b.com`)을 멘션으로 읽으면 주소를 칠 때마다 목록이 뜬다.
          allowedPrefixes: [' ', '\n', '(', '['],
          // 후보는 React 가 가져온다. 여기서 가져오면 캐시가 둘이 된다.
          items: () => [],
          render: () => ({
            onStart: (props) => {
              hooks.onChange({
                term: props.query,
                rect: props.clientRect?.() ?? null,
                pick: (user) => { insert(props.editor, props.range, user) },
              })
            },
            onUpdate: (props) => {
              hooks.onChange({
                term: props.query,
                rect: props.clientRect?.() ?? null,
                pick: (user) => { insert(props.editor, props.range, user) },
              })
            },
            onKeyDown: (props) => hooks.onKeyDown(props.event),
            onExit: () => { hooks.onChange(null) },
          }),
        }),
      ]
    },
  })
}
