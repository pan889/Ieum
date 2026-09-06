/**
 * 서식 모드의 자동완성 뼈대 — `@` 멘션과 `/` 삽입 명령이 같이 쓴다.
 *
 * 둘은 여는 글자와 넣는 것만 다르다. 따로 두면 목록 여는 조건과 키 처리가
 * 두 벌이 되고, 한쪽만 고쳐진다.
 *
 * 목록 UI 는 React 가 그린다. 여기서는 "언제 열고 무엇을 넘길지" 만 정하고
 * 상태를 밖으로 던진다 — tippy 같은 팝업 라이브러리를 하나 더 들이지 않기
 * 위해서다. 넣는 일도 밖에서 한다: 여기서 하려면 무엇을 넣을지까지 알아야
 * 하고, 그러면 이 파일이 멘션과 명령을 둘 다 알게 된다.
 */

import { Extension } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import Suggestion from '@tiptap/suggestion'
import type { Editor, Range } from '@tiptap/core'

export interface SuggestState {
  /** 여는 글자 뒤에 입력된 글자. 빈 문자열이면 방금 연 것이다. */
  term: string
  /** 화면에서 목록을 놓을 자리. */
  rect: DOMRect | null
  editor: Editor
  /** 갈아 끼울 범위. 여는 글자부터 커서까지다. */
  range: Range
}

export interface SuggestOptions {
  /** 확장 이름. 편집기 안에서 겹치면 안 된다. */
  name: string
  char: string
  /** 줄 맨 앞에서만 여는가. 명령은 그렇고 멘션은 아니다. */
  startOfLine?: boolean
  /** 이 글자 뒤에서만 연다. `null` 이면 어디서나. */
  allowedPrefixes?: string[] | null
  onChange: (state: SuggestState | null) => void
}

export function suggestExtension(options: SuggestOptions): Extension {
  const { name, char, startOfLine = false, allowedPrefixes, onChange } = options
  return Extension.create({
    name,
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          // 이름을 안 주면 둘 다 기본 키(`suggestion$`)를 쓴다. 같은 편집기에
          // 같은 키로 두 번 붙으면 ProseMirror 가 통째로 던진다 — 화면이
          // "무언가 잘못됐습니다" 한 줄로 바뀐다.
          pluginKey: new PluginKey(name),
          char,
          startOfLine,
          ...(allowedPrefixes === undefined ? {} : { allowedPrefixes }),
          // 후보는 React 가 가져온다. 여기서 가져오면 캐시가 둘이 된다.
          items: () => [],
          render: () => {
            const publish = (props: {
              query: string
              clientRect?: (() => DOMRect | null) | null
              editor: Editor
              range: Range
            }) => {
              onChange({
                term: props.query,
                rect: props.clientRect?.() ?? null,
                editor: props.editor,
                range: props.range,
              })
            }
            return {
              onStart: publish,
              onUpdate: publish,
              // 키는 React 가 캡처 단계에서 본다. 여기서 받으면 "지금 무엇이
              // 골라져 있는지" 를 렌더 밖으로 실어 날라야 한다.
              onKeyDown: () => false,
              onExit: () => { onChange(null) },
            }
          },
        }),
      ]
    },
  })
}
