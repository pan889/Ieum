import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import type { IqlSuggestion } from '@ieum/api-client'
import { searchApi } from '@/shared/api'

import { applySuggestion, moveActive, samePlace } from './iqlComplete'

/** 손을 멈춘 뒤 물어보기까지. 한 글자마다 물으면 요청이 타이핑을 따라온다. */
const ASK_DELAY_MS = 120
const MAX_OPTIONS = 8

interface Place {
  iql: string
  offset: number
}

export interface IqlEditorProps {
  value: string
  onChange: (next: string) => void
  onRun: () => void
  label: string
  placeholder: string
}

/**
 * IQL 입력창 + 자동완성.
 *
 * 무엇을 제안할지는 서버가 정한다 (`POST /api/v1/iql/suggest`). 문법이 서버에
 * 있으니 후보도 거기서 나와야 한다 — 여기서 판단하면 문법이 자랄 때마다 둘이
 * 갈라진다.
 *
 * 키보드만으로 끝나야 한다 (ux-principles 3절): ↑↓ 로 고르고, Enter·Tab 으로
 * 넣고, Esc 로 닫고, Ctrl/Cmd+Enter 로 실행한다. 고르면 커서가 다음 자리로
 * 가고 그 자리의 후보가 곧바로 뜬다.
 */
export function IqlEditor({ value, onChange, onRun, label, placeholder }: IqlEditorProps) {
  const { t } = useTranslation(['issues'])
  const id = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [place, setPlace] = useState<Place | null>(null)
  const [asked, setAsked] = useState<Place | null>(null)
  const [active, setActive] = useState(0)

  // 손을 멈춘 뒤에 묻는다. 커서가 그대로면 다시 묻지 않는다.
  useEffect(() => {
    if (place === null) return
    const timer = setTimeout(() => { setAsked(place) }, ASK_DELAY_MS)
    return () => { clearTimeout(timer) }
  }, [place])

  const suggestions = useQuery({
    queryKey: ['iql', 'suggest', asked?.iql, asked?.offset],
    queryFn: () =>
      searchApi.suggest({
        iql: asked?.iql ?? '',
        offset: asked?.offset ?? 0,
        limit: MAX_OPTIONS,
      }),
    enabled: asked !== null,
    staleTime: 30_000,
  })

  // 커서를 옮기는 사이의 낡은 목록을 띄우지 않는다.
  const fresh = place !== null && samePlace(asked, place)
  const options: IqlSuggestion[] = fresh ? (suggestions.data?.items ?? []) : []
  const open = options.length > 0
  const highlighted = options[Math.min(active, options.length - 1)]

  const sync = (element: HTMLTextAreaElement) => {
    setPlace({ iql: element.value, offset: element.selectionStart })
    setActive(0)
  }

  const close = () => { setPlace(null) }

  const choose = (item: IqlSuggestion) => {
    const range = suggestions.data
    const element = textarea.current
    // 범위는 물어본 그 본문에 대한 것이다. 그 사이에 글자가 바뀌었으면
    // 끼우지 않는다 — 엉뚱한 자리를 갈아 끼우면 쓴 글을 망가뜨린다.
    if (!range || !element || asked?.iql !== value) return
    const next = applySuggestion(value, range, item)
    onChange(next.text)
    setActive(0)
    // 값이 반영된 뒤라야 커서를 옮길 수 있다. 옮기고 나서 그 자리의 후보를
    // 곧바로 묻는다 — 고른 다음에 다시 손을 대야 하면 자동완성이 아니다.
    requestAnimationFrame(() => {
      element.focus()
      element.setSelectionRange(next.caret, next.caret)
      setPlace({ iql: next.text, offset: next.caret })
    })
  }

  return (
    <div className="relative flex-1">
      <textarea
        ref={textarea}
        aria-label={label}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={
          open ? `${id}-option-${String(options.indexOf(highlighted as IqlSuggestion))}` : undefined
        }
        className="min-h-16 w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-fg"
        placeholder={placeholder}
        value={value}
        onChange={(event) => {
          onChange(event.target.value)
          sync(event.target)
        }}
        onClick={(event) => { sync(event.currentTarget) }}
        onBlur={() => {
          // 목록의 버튼을 누르는 동안 blur 가 먼저 온다. 바로 닫으면 클릭이
          // 사라진다.
          requestAnimationFrame(() => { close() })
        }}
        onKeyDown={(event) => {
          // 실행은 목록이 열려 있든 아니든 같은 키다.
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault()
            close()
            onRun()
            return
          }
          if (event.key === ' ' && (event.metaKey || event.ctrlKey)) {
            // 목록을 닫아 둔 채로도 불러낼 수 있어야 한다.
            event.preventDefault()
            sync(event.currentTarget)
            return
          }
          if (!open) return
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setActive((index) => moveActive(index, 1, options.length))
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => moveActive(index, -1, options.length))
          } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault()
            if (highlighted) choose(highlighted)
          } else if (event.key === 'Escape') {
            event.preventDefault()
            close()
          }
        }}
      />
      {open ? (
        <ul
          id={`${id}-list`}
          role="listbox"
          aria-label={t('issues:filter.suggestions')}
          className="absolute left-2 top-full z-10 mt-1 max-h-72 w-80 overflow-y-auto rounded-md border border-border bg-surface shadow-lg"
        >
          {options.map((item, index) => (
            <li key={`${item.kind}:${item.insert}`}>
              <button
                type="button"
                role="option"
                id={`${id}-option-${String(index)}`}
                aria-selected={item === highlighted}
                className={clsx(
                  'flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-sm',
                  item === highlighted ? 'bg-surface-raised text-fg' : 'text-muted',
                )}
                onMouseDown={(event) => { event.preventDefault() }}
                onClick={() => { choose(item) }}
              >
                <span className="font-mono">{item.label}</span>
                {item.detail ? (
                  <span className="truncate text-xs text-muted">{item.detail}</span>
                ) : null}
                <span className="ml-auto shrink-0 text-xs text-muted">
                  {t(`issues:filter.kind.${item.kind}`)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
