import { useEffect, useMemo, useState } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import { Extension, Node } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import { attachmentsApi } from '@/shared/api'
import { useUserSearch } from '@/features/issues/hooks'

import { isText, toDoc, toMarkdown, type ElementNode } from './doc'
import { copiedFromEditor, pastedMarkdown } from './html'
import { insertMention, type MentionTarget } from './wysiwygMention'
import { insertSlash } from './wysiwygSlash'
import { suggestExtension, type SuggestState } from './wysiwygSuggest'
import { matchSlash, slashItems, type SlashItem } from './slash'
import { attachmentUri, internalLabel, internalUri, looksLikeUrl } from './paste'

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

/**
 * 목록의 촘촘함을 편집기가 들고 있게 한다.
 *
 * 마크다운에는 촘촘한 목록과 성근 목록이 따로 있는데(항목 사이 빈 줄),
 * 편집기 스키마에는 그런 것이 없다. 속성으로 넣어 두지 않으면 서식 모드에서
 * 글자 하나만 고쳐도 성근 목록이 촘촘해진다 — 편집기를 거쳤다는 이유로
 * 문서가 달라지면 안 된다(ADR-0008).
 */
const Tightness = Extension.create({
  name: 'ieumTightness',
  addGlobalAttributes() {
    return [
      {
        types: ['bulletList', 'orderedList', 'taskList'],
        attributes: {
          tight: {
            default: true,
            parseHTML: (element: HTMLElement) => element.getAttribute('data-tight') !== 'false',
            renderHTML: (attributes: Record<string, unknown>) =>
              attributes['tight'] === false ? { 'data-tight': 'false' } : {},
          },
        },
      },
    ]
  },
})

/** 붙여넣은 이미지를 매달 곳. 아직 저장 전인 화면에는 없다. */
export interface AttachTo {
  ownerType: string
  ownerId: string
}

export interface WysiwygProps {
  value: string
  onChange: (next: string) => void
  label: string
  attachTo?: AttachTo | undefined
}

const MAX_OPTIONS = 6

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
export function Wysiwyg({ value, onChange, label, attachTo }: WysiwygProps) {
  const { t } = useTranslation(['common'])
  const [mention, setMention] = useState<SuggestState | null>(null)
  const [slash, setSlash] = useState<SuggestState | null>(null)
  const [active, setActive] = useState(0)
  const [uploading, setUploading] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  const candidates = useUserSearch(mention?.term ?? '')
  // 매 렌더마다 새 배열이면 아래 키 처리 효과가 매번 다시 붙는다.
  const found = candidates.data?.items
  const people: MentionTarget[] = useMemo(
    () => (mention === null ? [] : (found ?? []).slice(0, MAX_OPTIONS)),
    [mention, found],
  )
  const commands: SlashItem[] = useMemo(
    () => (slash === null ? [] : matchSlash(slashItems(), slash.term).slice(0, MAX_OPTIONS)),
    [slash],
  )

  // 목록은 React 가 그리므로 키도 React 가 잡는다. 플러그인 쪽 `onKeyDown`
  // 으로 넘기면 "지금 무엇이 골라져 있는지" 를 렌더 밖으로 실어 날라야 하고,
  // 그러려면 렌더 중에 읽히는 상자가 하나 생긴다.
  const extensions = useMemo(
    () => [
      suggestExtension({
        name: 'ieumMention',
        char: '@',
        // 이메일(`a@b.com`)을 멘션으로 읽으면 주소를 칠 때마다 목록이 뜬다.
        allowedPrefixes: [' ', '\n', '(', '['],
        onChange: (state) => { setMention(state); setActive(0) },
      }),
      suggestExtension({
        name: 'ieumSlash',
        char: '/',
        // 줄 맨 앞에서만. 아무 데서나 열면 경로(`src/shared`)를 칠 때마다 뜬다.
        startOfLine: true,
        onChange: (state) => { setSlash(state); setActive(0) },
      }),
    ],
    [],
  )

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
      Tightness,
      ...extensions,
    ],
    content: toDoc(value),
    editorProps: {
      attributes: {
        'aria-label': label,
        class: 'ieum-markdown min-h-40 rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg focus:outline-none',
      },
      handlePaste: (_view, event) => handlePaste(event),
      handleDrop: (_view, event) => handleDrop(event),
    },
    onUpdate: ({ editor: instance }) => {
      onChange(toMarkdown(instance.getJSON() as ElementNode))
    },
  })

  /**
   * 이미지를 붙여넣거나 끌어다 놓으면 첨부로 올린다.
   *
   * 아직 저장 전인 화면(새 이슈·새 문서)에는 매달 주인이 없다. 그런 자리에서는
   * 조용히 삼키지 않고 "저장한 뒤에 붙일 수 있다" 고 말한다 — 붙인 줄 알았는데
   * 없는 것이 제일 나쁘다.
   */
  const attach = (files: File[]): boolean => {
    const images = files.filter((file) => file.type.startsWith('image/'))
    if (images.length === 0) return false
    if (!attachTo) {
      setFailed(t('common:editor.pasteNeedsOwner'))
      return true
    }
    setFailed(null)
    void (async () => {
      for (const file of images) {
        setUploading(file.name)
        try {
          const saved = await attachmentsApi.upload(attachTo.ownerType, attachTo.ownerId, file)
          editor
            .chain()
            .focus()
            .setImage({ src: attachmentUri(saved.id, saved.filename), alt: saved.filename })
            .run()
        } catch {
          setFailed(t('common:editor.pasteFailed', { name: file.name }))
        } finally {
          setUploading(null)
        }
      }
    })()
    return true
  }

  const handlePaste = (event: ClipboardEvent): boolean => {
    const files = [...(event.clipboardData?.files ?? [])]
    if (attach(files)) return true

    const html = event.clipboardData?.getData('text/html') ?? ''
    // 편집기끼리는 편집기가 하게 둔다. 문장 중간을 복사한 조각까지 우리가
    // 변환하면 이어 붙지 않고 새 문단이 된다.
    const slice = copiedFromEditor(html)
    if (!slice && insertHtml(html)) return true

    const text = event.clipboardData?.getData('text/plain') ?? ''
    if (looksLikeUrl(text)) {
      // 우리 주소는 스킴으로 바꿔 저장한다. 호스트가 바뀌어도 안 죽는다.
      const internal = internalUri(text)
      const href = internal ?? text.trim()
      const { from, to } = editor.state.selection
      if (from === to) {
        editor.chain().focus().insertContent({
          type: 'text',
          // 붙여넣은 사람이 뜻한 것은 그 이슈지 그 주소가 아니다.
          text: internal ? internalLabel(internal) : text.trim(),
          marks: [{ type: 'link', attrs: { href } }],
        }).run()
      } else {
        // 고른 글자가 있으면 그 글자를 링크로 만든다 — 주소로 덮어쓰지 않는다.
        editor.chain().focus().setLink({ href }).run()
      }
      return true
    }

    // 손대지 않기로 한 HTML 을 편집기가 자기 식으로 읽게 두지 않는다. `div`
    // 하나가 문단 하나가 되어 줄 사이에 빈 줄이 끼고 들여쓰기가 사라진다 —
    // 코드를 복사해 온 경우가 딱 그것이다. 소스 모드와 같이 평문으로 넣는다.
    if (!slice && html.trim() !== '' && text !== '') {
      editor.chain().focus().insertContent({ type: 'text', text }).run()
      return true
    }
    return false
  }

  /**
   * 서식 있는 HTML 을 마크다운으로 바꿔 끼운다.
   *
   * TipTap 도 HTML 을 읽을 줄 안다. 그런데 그쪽으로 두면 소스 모드와 결과가
   * 갈린다 — 편집기 스키마가 받아 주는 것과 우리 마크다운이 담을 수 있는
   * 것이 다르기 때문이다. 같은 클립보드에서 같은 문서가 나와야 한다.
   */
  const insertHtml = (html: string): boolean => {
    const markdown = pastedMarkdown(html)
    if (markdown === null) return false
    const content = toDoc(markdown).content ?? []
    const only = content.length === 1 ? content[0] : undefined
    // 문단 하나면 쓰던 문장 안에 그대로 잇는다. 블록으로 넣으면 문단이 갈린다.
    const paragraph = only !== undefined && !isText(only) && only.type === 'paragraph' ? only : null
    editor.chain().focus().insertContent(paragraph ? (paragraph.content ?? []) : content).run()
    return true
  }

  const handleDrop = (event: DragEvent): boolean => attach([...(event.dataTransfer?.files ?? [])])

  /**
   * 열려 있는 목록.
   *
   * 둘이 같이 열릴 수는 없다 — `/` 명령에는 공백도 `@` 도 못 들어가므로,
   * 하나가 열리는 순간 다른 하나는 닫혀 있다. 매 렌더 새 객체를 만들면 아래
   * 키 처리 효과가 매번 다시 붙는다.
   */
  const open = useMemo(() => {
    if (slash !== null && commands.length > 0) {
      return {
        count: commands.length,
        close: () => { setSlash(null) },
        pick: (index: number) => {
          const chosen = commands[index]
          if (chosen) insertSlash(slash.editor, slash.range, chosen)
        },
      }
    }
    if (mention !== null && people.length > 0) {
      return {
        count: people.length,
        close: () => { setMention(null) },
        pick: (index: number) => {
          const chosen = people[index]
          if (chosen) insertMention(mention.editor, mention.range, chosen)
        },
      }
    }
    return null
  }, [slash, commands, mention, people])

  /**
   * 목록이 열려 있는 동안의 키. 편집기보다 **먼저** 본다.
   *
   * 캡처 단계로 붙인다. ProseMirror 는 같은 요소에 일반 리스너를 달아 두므로,
   * 여기서 `stopImmediatePropagation` 을 해야 방향키가 커서를 움직이지 않는다.
   */
  useEffect(() => {
    if (open === null) return
    const dom = editor.view.dom
    const onKeyDown = (event: KeyboardEvent) => {
      const stop = () => {
        event.preventDefault()
        event.stopImmediatePropagation()
      }
      if (event.key === 'ArrowDown') {
        stop()
        setActive((index) => (index + 1) % open.count)
      } else if (event.key === 'ArrowUp') {
        stop()
        setActive((index) => (index - 1 + open.count) % open.count)
      } else if (event.key === 'Enter' || event.key === 'Tab') {
        stop()
        open.pick(Math.min(active, open.count - 1))
      } else if (event.key === 'Escape') {
        stop()
        open.close()
      }
    }
    dom.addEventListener('keydown', onKeyDown, true)
    return () => { dom.removeEventListener('keydown', onKeyDown, true) }
  }, [editor, open, active])

  // 밖에서 본문이 바뀌면(초안 복원·판 되돌리기) 따라간다. 같은 글이면
  // 건드리지 않는다 — 매번 다시 심으면 커서가 문서 앞으로 튄다.
  useEffect(() => {
    if (toMarkdown(editor.getJSON() as ElementNode) === value) return
    editor.commands.setContent(toDoc(value), { emitUpdate: false })
  }, [editor, value])

  const highlighted = open === null ? -1 : Math.min(active, open.count - 1)

  return (
    <div className="relative">
      <EditorContent editor={editor} />
      {uploading !== null ? (
        <p className="mt-1 text-xs text-muted">
          {t('common:editor.pasteUploading', { name: uploading })}
        </p>
      ) : null}
      {failed !== null ? <p className="mt-1 text-xs text-danger">{failed}</p> : null}
      {open !== null && slash !== null ? (
        <Listbox
          label={t('common:editor.slashList')}
          rect={slash.rect}
          highlighted={highlighted}
          entries={commands.map((entry) => ({
            key: entry.id,
            text: t(`common:slash.${entry.id}`),
          }))}
          onPick={open.pick}
        />
      ) : open !== null && mention !== null ? (
        <Listbox
          label={t('common:editor.mentionList')}
          rect={mention.rect}
          highlighted={highlighted}
          entries={people.map((user) => ({ key: user.id, text: user.display_name }))}
          onPick={open.pick}
        />
      ) : null}
    </div>
  )
}

/** 캐럿 아래 목록. 멘션과 `/` 명령이 같이 쓴다. */
function Listbox({
  label,
  rect,
  entries,
  highlighted,
  onPick,
}: {
  label: string
  rect: DOMRect | null
  entries: { key: string; text: string }[]
  highlighted: number
  onPick: (index: number) => void
}) {
  return (
    <ul
      role="listbox"
      aria-label={label}
      className="absolute z-10 w-64 overflow-hidden rounded-md border border-border bg-surface shadow-lg"
      style={caretPosition(rect)}
    >
      {entries.map((entry, index) => (
        <li key={entry.key}>
          <button
            type="button"
            role="option"
            aria-selected={index === highlighted}
            className={clsx(
              'w-full px-3 py-1.5 text-left text-sm',
              index === highlighted ? 'bg-surface-raised text-fg' : 'text-muted',
            )}
            // 누르는 동안 편집기가 포커스를 잃으면 목록이 먼저 닫힌다.
            onMouseDown={(event) => { event.preventDefault() }}
            onClick={() => { onPick(index) }}
          >
            {entry.text}
          </button>
        </li>
      ))}
    </ul>
  )
}

/**
 * 목록을 캐럿 아래에 놓는다.
 *
 * `clientRect` 는 화면 좌표라 스크롤한 문서에서는 그대로 쓸 수 없다. 편집기
 * 상자를 기준으로 옮긴다.
 */
function caretPosition(rect: DOMRect | null): { left: number; top: number } {
  if (!rect) return { left: 8, top: 8 }
  return { left: rect.left + window.scrollX, top: rect.bottom + window.scrollY + 4 }
}
