import { useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { useUserSearch } from '@/features/issues/hooks'

import { Markdown } from './Markdown'
import { Wysiwyg, type AttachTo } from './Wysiwyg'
import { pastedMarkdown } from './html'
import { applyMention, findMentionQuery, type MentionQuery } from './mention'
import {
  applySlash,
  findSlashQuery,
  matchSlash,
  slashItems,
  type SlashItem,
  type SlashQuery,
} from './slash'

/** 서식(WYSIWYG) · 마크다운 · 미리보기. 순서가 곧 탭 순서다. */
const MODES = ['rich', 'write', 'preview'] as const
type Mode = (typeof MODES)[number]

/**
 * 자동완성 목록. 멘션과 `/` 명령이 같은 모양으로 그려진다.
 *
 * 무엇을 하는지(고르기·닫기)는 담지 않는다. 그 함수들이 `textarea` 참조를
 * 읽으므로, 렌더 중에 읽히는 값에 넣으면 참조를 렌더에서 만지게 된다.
 */
interface OpenList {
  label: string
  entries: { key: string; text: string; hint?: string }[]
}

/**
 * 커서 자리에 글자를 끼운다.
 *
 * `execCommand` 는 낡았지만, 되돌리기 이력을 남기는 방법이 이것뿐이다. 값을
 * 직접 갈아 끼우면 Ctrl+Z 로 붙여넣기 직전으로 못 돌아간다 — 긴 글을 쓰다가
 * 잘못 붙여넣은 사람에게는 그게 전부다. 못 쓰는 브라우저에서는 값을 바꾼다.
 */
function insertAtCaret(
  element: HTMLTextAreaElement,
  text: string,
  onChange: (next: string) => void,
): void {
  element.focus()
  // eslint-disable-next-line @typescript-eslint/no-deprecated -- 되돌리기 이력을 남기는 유일한 방법
  if (document.execCommand('insertText', false, text)) return
  const start = element.selectionStart
  const end = element.selectionEnd
  onChange(element.value.slice(0, start) + text + element.value.slice(end))
  const caret = start + text.length
  requestAnimationFrame(() => { element.setSelectionRange(caret, caret) })
}

/**
 * 마크다운 편집기. 서식 모드·소스 모드·미리보기.
 *
 * **정본은 언제나 마크다운이다**(ADR-0008). 서식 모드는 같은 문자열을 다르게
 * 보여줄 뿐이고, 그 왕복이 문서를 바꾸지 않는다는 것은 코퍼스로 잠가 두었다
 * (doc.test.ts). 소스 모드는 상시 제공한다 (ux-principles 6절).
 *
 * 기본은 서식이다. `@` 멘션과 붙여넣기(이미지 → 첨부, 주소 → 링크)가 서식
 * 모드에도 붙어서, 이제 소스 모드에서만 되는 일이 없다.
 */
export function MarkdownEditor({
  label,
  value,
  onChange,
  placeholder,
  rows = 8,
  attachTo,
  sourceRef,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  placeholder?: string
  rows?: number
  /** 붙여넣은 이미지를 매달 곳. 아직 저장 전인 화면에는 없다. */
  attachTo?: AttachTo | undefined
  /**
   * 소스 모드의 textarea. **동시 편집이 캐럿을 되돌려 놓는 데 쓴다** (B16).
   *
   * 값만 controlled 로 두면 남의 편집이 들어올 때마다 브라우저가 캐럿을 끝으로
   * 보낸다 — 같이 쓰는 자리에서 가장 빨리 포기하게 되는 증상이다. 그것을
   * 고치려면 밖에서 선택 구간을 읽고 다시 놓아야 한다.
   */
  sourceRef?: ((element: HTMLTextAreaElement | null) => void) | undefined
}) {
  const { t } = useTranslation(['common'])
  const [tab, setTab] = useState<Mode>('rich')
  const id = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [mention, setMention] = useState<MentionQuery | null>(null)
  const [slash, setSlash] = useState<SlashQuery | null>(null)
  const [highlight, setHighlight] = useState(0)

  // 멘션 후보. 진행 중인 멘션이 없으면 조회하지 않는다.
  const candidates = useUserSearch(mention?.term ?? '')
  const people = mention === null ? [] : (candidates.data?.items ?? []).slice(0, 6)
  const commands = slash === null ? [] : matchSlash(slashItems(), slash.term).slice(0, 6)

  /**
   * 커서 앞을 보고 목록을 연다.
   *
   * 둘이 같이 열릴 수는 없다 — `/` 명령에는 공백도 `@` 도 못 들어간다.
   * 그래도 순서를 정해 둔다: 명령이 먼저다.
   */
  const sync = (element: HTMLTextAreaElement) => {
    const caret = element.selectionStart
    const command = findSlashQuery(element.value, caret)
    setSlash(command)
    setMention(command === null ? findMentionQuery(element.value, caret) : null)
    setHighlight(0)
  }

  const commit = (result: { text: string; caret: number }) => {
    const element = textarea.current
    if (element === null) return
    onChange(result.text)
    setMention(null)
    setSlash(null)
    // 값이 바뀐 뒤에 커서를 놓아야 한다. React 가 다시 그리기 전에 옮기면
    // 렌더가 커서를 끝으로 되돌린다.
    requestAnimationFrame(() => {
      element.setSelectionRange(result.caret, result.caret)
      element.focus()
    })
  }

  const chooseMention = (user: { id: string; display_name: string }) => {
    const element = textarea.current
    if (element === null || mention === null) return
    commit(applyMention(element.value, mention, element.selectionStart, user))
  }

  const chooseCommand = (chosen: SlashItem) => {
    const element = textarea.current
    if (element === null || slash === null) return
    commit(applySlash(element.value, slash, element.selectionStart, chosen))
  }

  /** 열려 있는 목록. 키 처리와 그리기가 이것만 본다. */
  const open: OpenList | null =
    commands.length > 0
      ? {
          label: t('common:editor.slashList'),
          entries: commands.map((entry) => ({
            key: entry.id,
            text: t(`common:slash.${entry.id}`),
          })),
        }
      : people.length > 0
        ? {
            label: t('common:editor.mentionList'),
            entries: people.map((user) => ({
              key: user.id,
              text: user.display_name,
              hint: user.email,
            })),
          }
        : null

  const pick = (index: number) => {
    if (commands.length > 0) {
      const chosen = commands[index]
      if (chosen) chooseCommand(chosen)
      return
    }
    const chosen = people[index]
    if (chosen) chooseMention(chosen)
  }

  const closeList = () => {
    setMention(null)
    setSlash(null)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-3">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
        {/* 탭 목록에 필드 이름을 주면 textarea 의 라벨과 이름이 겹친다.
            서로 다른 컨트롤이 같은 이름을 가지면 구분할 방법이 없다. */}
        <div
          role="tablist"
          aria-label={t('common:editor.mode')}
          className="ml-auto flex gap-1 text-xs"
        >
          {MODES.map((name) => (
            <button
              key={name}
              id={`${id}-tab-${name}`}
              type="button"
              role="tab"
              aria-selected={tab === name}
              aria-controls={`${id}-panel`}
              className={clsx(
                'rounded px-2 py-1',
                tab === name ? 'bg-surface-raised font-medium text-fg' : 'text-muted hover:text-fg',
              )}
              onClick={() => { setTab(name); }}
            >
              {t(`common:editor.${name}`)}
            </button>
          ))}
        </div>
      </div>

      <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`}>
        {tab === 'rich' ? (
          <Wysiwyg value={value} onChange={onChange} label={label} attachTo={attachTo} />
        ) : tab === 'write' ? (
          <div className="relative">
            <textarea
              ref={(element) => {
                textarea.current = element
                sourceRef?.(element)
              }}
              id={id}
              rows={rows}
              placeholder={placeholder}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-fg placeholder:text-muted"
              value={value}
              onChange={(event) => {
                onChange(event.target.value)
                sync(event.target)
              }}
              onClick={(event) => { sync(event.currentTarget); }}
              onPaste={(event) => {
                // 서식 있는 HTML 을 그냥 두면 브라우저가 평문만 떨어뜨린다 —
                // 제목·표·링크가 통째로 사라진다. 서식 모드와 **같은 변환**을
                // 거쳐 마크다운으로 넣는다.
                const markdown = pastedMarkdown(event.clipboardData.getData('text/html'))
                if (markdown === null) return
                event.preventDefault()
                insertAtCaret(event.currentTarget, markdown, onChange)
              }}
              onBlur={() => {
                // 목록의 버튼을 누르는 동안 blur 가 먼저 온다. 바로 닫으면
                // 클릭이 사라지므로 한 프레임 미룬다.
                requestAnimationFrame(() => { setMention(null); setSlash(null) })
              }}
              onKeyDown={(event) => {
                if (open === null) return
                const count = open.entries.length
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setHighlight((h) => (h + 1) % count)
                } else if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setHighlight((h) => (h - 1 + count) % count)
                } else if (event.key === 'Enter' || event.key === 'Tab') {
                  event.preventDefault()
                  pick(Math.min(highlight, count - 1))
                } else if (event.key === 'Escape') {
                  closeList()
                }
              }}
            />
            {open !== null ? (
              <ul
                role="listbox"
                aria-label={open.label}
                className="absolute left-2 top-full z-10 mt-1 w-64 overflow-hidden rounded-md border border-border bg-surface shadow-lg"
              >
                {open.entries.map((entry, index) => (
                  <li key={entry.key}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={index === highlight}
                      className={clsx(
                        'w-full px-3 py-1.5 text-left text-sm',
                        index === highlight ? 'bg-surface-raised text-fg' : 'text-muted',
                      )}
                      onMouseDown={(event) => { event.preventDefault(); }}
                      onClick={() => { pick(index); }}
                    >
                      {entry.text}
                      {entry.hint ? (
                        <span className="ml-2 text-xs text-muted">{entry.hint}</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <div className="min-h-24 rounded-md border border-border bg-surface px-3 py-2">
            {value.trim() ? (
              <Markdown source={value} className="text-sm" />
            ) : (
              <p className="text-sm text-muted">{t('common:editor.previewEmpty')}</p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-muted">
        {t(tab === 'rich' ? 'common:editor.richHint' : 'common:editor.markdownHint')}
      </p>
    </div>
  )
}
