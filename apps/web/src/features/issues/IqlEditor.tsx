import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import type { IqlSuggestion } from '@ieum/api-client'
import { searchApi } from '@/shared/api'
import { describeErrorCode } from '@/shared/api/errors'
import { Alert } from '@/shared/ui/primitives'

import { applySuggestion, moveActive, samePlace } from './iqlComplete'
import { applySuggestion as replaceSpan, excerpt } from './iqlError'

/** 손을 멈춘 뒤 물어보기까지. 한 글자마다 물으면 요청이 타이핑을 따라온다. */
const ASK_DELAY_MS = 120
/** 오류는 더 늦게 말한다. 제안은 도움이지만 빨간 글씨는 재촉이다. */
const CHECK_DELAY_MS = 400
const MAX_OPTIONS = 8
/** 글자를 안 바꾸고 자리만 옮기는 키. 이것도 "지금 어디" 가 바뀐 것이다. */
const CARET_KEYS = new Set([
  'ArrowLeft',
  'ArrowRight',
  'ArrowUp',
  'ArrowDown',
  'Home',
  'End',
  'PageUp',
  'PageDown',
])

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
  /** 실행이 실패했을 때의 문구. 자리를 아는 오류가 있으면 그쪽이 이긴다. */
  invalid?: string | undefined
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
export function IqlEditor({
  value,
  onChange,
  onRun,
  label,
  placeholder,
  invalid,
}: IqlEditorProps) {
  const { t } = useTranslation(['issues'])
  const id = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [place, setPlace] = useState<Place | null>(null)
  const [asked, setAsked] = useState<Place | null>(null)
  const [active, setActive] = useState(0)
  const [checking, setChecking] = useState(value)

  // 손을 멈춘 뒤에 묻는다. 커서가 그대로면 다시 묻지 않는다.
  useEffect(() => {
    if (place === null) return
    const timer = setTimeout(() => { setAsked(place) }, ASK_DELAY_MS)
    return () => { clearTimeout(timer) }
  }, [place])

  // 검증도 손을 멈춘 뒤에 묻는다. 커서만 움직였을 때는 다시 묻지 않는다 —
  // 본문이 그대로면 답도 그대로다.
  useEffect(() => {
    const timer = setTimeout(() => { setChecking(value) }, CHECK_DELAY_MS)
    return () => { clearTimeout(timer) }
  }, [value])

  const validation = useQuery({
    queryKey: ['iql', 'validate', checking],
    queryFn: () => searchApi.validate(checking),
    enabled: checking.trim() !== '',
    staleTime: 30_000,
  })

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

  // 검증 결과는 물어본 그 본문에 대한 것이다. 그 사이에 글자가 바뀌었으면
  // 자리도 어긋나 있다 — 엉뚱한 곳에 캐럿을 찍느니 아무것도 안 찍는다.
  const answered =
    checking === value && validation.data && !validation.data.valid ? validation.data.error : null
  // 끝에서 계속 치는 중이면 "아직 덜 썼다" 는 뜻이다. 그 상태의 문법 오류는
  // 알림이 아니라 재촉이다 — `project = ` 를 칠 때마다 빨간 글씨가 뜬다.
  // 뜻이 틀린 오류(모르는 필드·못 쓰는 연산자)는 덜 썼든 아니든 틀린 것이라
  // 그대로 말한다.
  const composing = place !== null && place.iql === value && place.offset === value.length
  const problem = answered?.code === 'iql.syntax_error' && composing ? null : answered
  const span =
    problem && typeof problem.offset === 'number'
      ? { offset: problem.offset, length: problem.length ?? 1 }
      : null
  const marked = span ? excerpt(value, span) : null
  const suggested = Array.isArray(problem?.suggestions) ? problem.suggestions : []

  /** 틀린 자리를 입력창에서 골라 준다. 커서가 거기로 간다. */
  const goToProblem = () => {
    const element = textarea.current
    if (!element || !span) return
    element.focus()
    element.setSelectionRange(span.offset, span.offset + span.length)
  }

  const fix = (replacement: string) => {
    const element = textarea.current
    if (!element || !span) return
    const next = replaceSpan(value, span, replacement)
    onChange(next.text)
    requestAnimationFrame(() => {
      element.focus()
      element.setSelectionRange(next.caret, next.caret)
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
        onKeyUp={(event) => {
          // 커서만 옮겨도 자리가 바뀐다. 목록이 열려 있을 때 방향키는 목록을
          // 고르는 키이므로 건드리지 않는다.
          if (!open && CARET_KEYS.has(event.key)) sync(event.currentTarget)
        }}
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

      {invalid && !problem ? (
        // 자리를 아는 오류가 있으면 같은 말을 두 번 하지 않는다. 여기 남는
        // 것은 위치가 없는 실패다(권한·네트워크·서버 오류).
        <div className="mt-1.5">
          <Alert>{invalid}</Alert>
        </div>
      ) : null}

      {problem ? (
        // 제안 목록이 열려 있어도 그대로 둔다. 목록은 그 위에 떠 있고, 감췄다
        // 보였다 하면 오류가 뜨는 조건이 오히려 알 수 없어진다.
        <div className="mt-1.5 flex flex-col gap-1 text-xs" role="status">
          <p className="text-danger">{describeErrorCode(problem.code, problem)}</p>
          {marked ? (
            // 캐럿 줄은 입력창과 같은 글꼴이어야 자리가 맞는다.
            <button
              type="button"
              className="w-full overflow-x-auto rounded bg-surface-raised px-2 py-1.5 text-left font-mono leading-snug text-muted"
              title={t('issues:filter.goToProblem')}
              onClick={goToProblem}
            >
              <span className="block whitespace-pre">{marked.line}</span>
              <span className="block whitespace-pre text-danger">{marked.caret}</span>
            </button>
          ) : null}
          {suggested.length > 0 ? (
            <p className="flex flex-wrap items-baseline gap-1.5 text-muted">
              <span>{t('issues:filter.didYouMean')}</span>
              {suggested.map((name) => (
                <button
                  key={name}
                  type="button"
                  className="rounded border border-border px-1.5 py-0.5 font-mono text-fg"
                  onClick={() => { fix(name) }}
                >
                  {name}
                </button>
              ))}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
