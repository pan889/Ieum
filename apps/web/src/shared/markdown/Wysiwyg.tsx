import { useEffect } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import { Node } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import { TaskItem, TaskList } from '@tiptap/extension-list'

import { toDoc, toMarkdown, type ElementNode } from './doc'

/**
 * 편집기가 다루지 않는 블록. 원문을 그대로 들고 있다가 그대로 내놓는다.
 *
 * front matter·디렉티브·각주 정의처럼 WYSIWYG 로 만질 수 없는 것들이다.
 * "못 다루니까 버린다" 가 아니라 "안 건드린다" 여야 문서가 안 깨진다
 * (doc.ts 참고). 내용을 편집하려면 소스 모드로 가면 된다.
 */
const Verbatim = Node.create({
  name: 'verbatim',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      source: {
        default: '',
        // 원문은 속성이 아니라 본문으로 담는다. 속성으로도 내보내면 같은 글이
        // 두 군데 있게 되고, 복사·붙여넣기에서 둘이 어긋난다.
        parseHTML: (element: HTMLElement) => element.textContent,
        renderHTML: () => ({}),
      },
    }
  },
  parseHTML() {
    return [{ tag: 'pre[data-verbatim]' }]
  },
  renderHTML({ node }) {
    return [
      'pre',
      { 'data-verbatim': 'true', class: 'ieum-verbatim' },
      String(node.attrs['source'] ?? ''),
    ]
  },
})

export interface WysiwygProps {
  value: string
  onChange: (next: string) => void
  label: string
}

/**
 * 보이는 대로 편집한다. **정본은 마크다운이다**(ADR-0008).
 *
 * 편집기 상태를 정본으로 삼지 않는 이유: 저장·검색·diff·`.md` 내보내기가 전부
 * 마크다운 위에서 돌아간다. 편집기가 정본이 되는 순간 그 전부가 편집기 문서
 * 모델을 알아야 한다.
 *
 * 그래서 밖에서는 마크다운 문자열만 오간다. 들어올 때 파싱하고 나갈 때
 * 직렬화하며, 그 왕복이 문서를 바꾸지 않는다는 것은 코퍼스로 잠가 두었다
 * (doc.test.ts).
 */
export function Wysiwyg({ value, onChange, label }: WysiwygProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // 링크는 우리 스킴(`page:`·`issue:`)을 살려야 한다. 자동 링크는 끄고
        // 마크다운에 적힌 링크만 쓴다.
        link: { openOnClick: false, autolink: false, validate: () => true },
        // 마크다운에 없는 것은 켜지 않는다. 켜 두면 편집기에서 만들 수 있는데
        // 저장하면 사라진다.
        underline: false,
      }),
      Image,
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader,
      TableCell,
      TaskList,
      TaskItem.configure({ nested: true }),
      Verbatim,
    ],
    content: toDoc(value),
    editorProps: {
      attributes: {
        'aria-label': label,
        class: 'ieum-markdown min-h-40 rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg focus:outline-none',
      },
    },
    onUpdate: ({ editor: instance }) => {
      onChange(toMarkdown(instance.getJSON() as ElementNode))
    },
  })

  // 밖에서 본문이 바뀌면(초안 복원·판 되돌리기) 따라간다. 같은 글이면
  // 건드리지 않는다 — 매번 다시 심으면 커서가 문서 앞으로 튄다.
  useEffect(() => {
    if (toMarkdown(editor.getJSON() as ElementNode) === value) return
    editor.commands.setContent(toDoc(value), { emitUpdate: false })
  }, [editor, value])

  return <EditorContent editor={editor} />
}
