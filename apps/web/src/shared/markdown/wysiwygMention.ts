/**
 * 서식 모드의 `@` 멘션 — 무엇을 끼우는지.
 *
 * 소스 모드의 `mention.ts` 와 **같은 결과**를 만든다:
 * `[@이름](user:<uuid>)`. 이름이 바뀌어도 안 깨지는 저장 형태가 하나여야
 * 두 모드가 같은 문서를 쓴다.
 *
 * 목록을 언제 여는지는 `wysiwygSuggest.ts` 가 정한다 — `/` 명령과 같은
 * 뼈대다.
 */

import type { Editor, Range } from '@tiptap/core'

export interface MentionTarget {
  id: string
  display_name: string
}

/** 고른 사람을 링크 마크가 붙은 글자로 끼운다. */
export function insertMention(editor: Editor, range: Range, user: MentionTarget): void {
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
