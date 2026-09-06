/**
 * 서식 모드의 `/` 삽입 명령 — 무엇을 끼우는지.
 *
 * 소스 모드와 **같은 마크다운**을 넣는다(`slash.ts`). 편집기 명령
 * (`setNode`·`insertTable` …)으로 따로 만들면 조각이 두 벌이 되고, 두 벌은
 * 반드시 갈라진다. 마크다운을 문서로 바꾸는 일은 이미 `doc.ts` 가 한다.
 */

import type { Editor, Range } from '@tiptap/core'

import { toDoc } from './doc'
import type { SlashItem } from './slash'

/**
 * 고른 조각을 끼운다.
 *
 * 넣을 자리에는 `/명령` 을 치던 빈 문단이 있다. 범위째 갈아 끼우므로 그
 * 문단은 남지 않는다 — 안 그러면 넣을 때마다 빈 줄이 하나씩 쌓인다.
 */
export function insertSlash(editor: Editor, range: Range, chosen: SlashItem): void {
  const content = toDoc(chosen.insert).content ?? []
  editor.chain().focus().insertContentAt(range, content).run()
}
