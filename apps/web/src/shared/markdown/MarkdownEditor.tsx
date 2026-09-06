import { useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { useUserSearch } from '@/features/issues/hooks'

import { Markdown } from './Markdown'
import { Wysiwyg } from './Wysiwyg'
import { applyMention, findMentionQuery, type MentionQuery } from './mention'

/** 서식(WYSIWYG) · 마크다운 · 미리보기. */
const MODES = ['write', 'rich', 'preview'] as const
type Mode = (typeof MODES)[number]

/**
 * 마크다운 편집기. 서식 모드·소스 모드·미리보기.
 *
 * **정본은 언제나 마크다운이다**(ADR-0008). 서식 모드는 같은 문자열을 다르게
 * 보여줄 뿐이고, 그 왕복이 문서를 바꾸지 않는다는 것은 코퍼스로 잠가 두었다
 * (doc.test.ts). 소스 모드는 상시 제공한다 (ux-principles 6절).
 *
 * 기본값이 아직 소스인 이유: `@` 멘션과 이미지 붙여넣기가 소스 모드에만
 * 있다. 기본을 서식으로 돌리면 이미 쓰던 기능이 사라진 것처럼 보인다 —
 * 그 둘이 서식 모드에도 붙으면 기본을 바꾼다.
 */
export function MarkdownEditor({
  label,
  value,
  onChange,
  placeholder,
  rows = 8,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  placeholder?: string
  rows?: number
}) {
  const { t } = useTranslation(['common'])
  const [tab, setTab] = useState<Mode>('write')
  const id = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [mention, setMention] = useState<MentionQuery | null>(null)
  const [highlight, setHighlight] = useState(0)

  // 멘션 후보. 진행 중인 멘션이 없으면 조회하지 않는다.
  const candidates = useUserSearch(mention?.term ?? '')
  const options = mention === null ? [] : (candidates.data?.items ?? []).slice(0, 6)

  const syncMention = (element: HTMLTextAreaElement) => {
    const found = findMentionQuery(element.value, element.selectionStart)
    setMention(found)
    setHighlight(0)
  }

  const choose = (user: { id: string; display_name: string }) => {
    const element = textarea.current
    if (element === null || mention === null) return
    const result = applyMention(element.value, mention, element.selectionStart, user)
    onChange(result.text)
    setMention(null)
    // 값이 바뀐 뒤에 커서를 놓아야 한다. React 가 다시 그리기 전에 옮기면
    // 렌더가 커서를 끝으로 되돌린다.
    requestAnimationFrame(() => {
      element.setSelectionRange(result.caret, result.caret)
      element.focus()
    })
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
          <Wysiwyg value={value} onChange={onChange} label={label} />
        ) : tab === 'write' ? (
          <div className="relative">
            <textarea
              ref={textarea}
              id={id}
              rows={rows}
              placeholder={placeholder}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-fg placeholder:text-muted"
              value={value}
              onChange={(event) => {
                onChange(event.target.value)
                syncMention(event.target)
              }}
              onClick={(event) => { syncMention(event.currentTarget); }}
              onBlur={() => {
                // 목록의 버튼을 누르는 동안 blur 가 먼저 온다. 바로 닫으면
                // 클릭이 사라지므로 한 프레임 미룬다.
                requestAnimationFrame(() => { setMention(null); })
              }}
              onKeyDown={(event) => {
                if (options.length === 0) return
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setHighlight((h) => (h + 1) % options.length)
                } else if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setHighlight((h) => (h - 1 + options.length) % options.length)
                } else if (event.key === 'Enter' || event.key === 'Tab') {
                  event.preventDefault()
                  choose(options[highlight] as { id: string; display_name: string })
                } else if (event.key === 'Escape') {
                  setMention(null)
                }
              }}
            />
            {options.length > 0 ? (
              <ul
                role="listbox"
                aria-label={t('common:editor.mentionList')}
                className="absolute left-2 top-full z-10 mt-1 w-64 overflow-hidden rounded-md border border-border bg-surface shadow-lg"
              >
                {options.map((user, index) => (
                  <li key={user.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={index === highlight}
                      className={clsx(
                        'w-full px-3 py-1.5 text-left text-sm',
                        index === highlight ? 'bg-surface-raised text-fg' : 'text-muted',
                      )}
                      onMouseDown={(event) => { event.preventDefault(); }}
                      onClick={() => { choose(user); }}
                    >
                      {user.display_name}
                      <span className="ml-2 text-xs text-muted">{user.email}</span>
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
