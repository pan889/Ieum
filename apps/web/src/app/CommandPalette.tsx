/**
 * 전역 명령 팔레트 (`Cmd/Ctrl+K`).
 *
 * 이 앱은 화면이 서른 개가 넘는다. 왼쪽 목록에 다 못 넣어서 설정 아래로
 * 들어간 것이 스물이고, 그것들은 **경로를 외우는 사람만** 간다. 팔레트는 그
 * 목록을 치는 것으로 바꾼다.
 *
 * 팔레트 안의 키(위/아래/Enter/Esc)는 **입력 칸의 `onKeyDown` 에서** 직접
 * 처리한다. 전역 디스패처에 얹지 않는 이유: 초점이 이 칸에 있으므로 전역
 * 단축키는 어차피 죽고(입력 칸 억제), 그렇다면 스코프 순서를 따질 일이 없는
 * 쪽이 간단하다.
 */

import { useNavigate } from '@tanstack/react-router'
import { useId, useMemo, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Card } from '@/shared/ui/primitives'

export interface Command {
  id: string
  label: string
  /** 목록에서 갈래를 알려 주는 짧은 말(이동·만들기·찾기). */
  kind: string
  run: () => void
}

interface Props {
  onClose: () => void
  /** 왼쪽 목록과 같은 곳들. 앱셸이 자기 것을 그대로 넘긴다. */
  places: readonly { to: string; label: string }[]
}

export function CommandPalette({ onClose, places }: Props) {
  const { t } = useTranslation(['common'])
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [at, setAt] = useState(0)
  const listId = useId()

  const commands = useMemo<Command[]>(() => {
    const typed = query.trim()
    const out: Command[] = places.map((place) => ({
      id: `go:${place.to}`,
      label: place.label,
      kind: t('common:keys.kindGo'),
      run: () => {
        void navigate({ to: place.to })
      },
    }))
    out.push({
      id: 'new:issue',
      label: t('common:keys.createIssue'),
      kind: t('common:keys.kindCreate'),
      run: () => {
        void navigate({ to: '/issues/new' })
      },
    })
    if (typed !== '') {
      // **친 것을 그대로 찾을 길을 항상 남긴다.** 이름이 안 맞아 목록이 비어도
      // 팔레트가 막다른 골목이 되지 않는다.
      out.push({
        id: 'search',
        label: t('common:keys.searchFor', { query: typed }),
        kind: t('common:keys.kindFind'),
        run: () => {
          void navigate({ to: '/search', search: { q: typed, offset: 0 } })
        },
      })
    }
    return out
  }, [places, query, navigate, t])

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (needle === '') return commands
    return commands.filter(
      (command) => command.id === 'search' || command.label.toLowerCase().includes(needle),
    )
  }, [commands, query])

  // 걸러내고 나면 고른 자리가 목록 밖일 수 있다. 렌더 중에 맞춘다 —
  // effect 로 미루면 한 프레임 동안 없는 항목이 골라진 것으로 그려진다.
  const cursor = shown.length === 0 ? 0 : Math.min(at, shown.length - 1)

  function runAt(index: number): void {
    const command = shown[index]
    if (command === undefined) return
    // **먼저 닫는다.** 이동이 먼저면 새 화면 위에 팔레트가 한 프레임 남는다.
    onClose()
    command.run()
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setAt(shown.length === 0 ? 0 : (cursor + 1) % shown.length)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setAt(shown.length === 0 ? 0 : (cursor - 1 + shown.length) % shown.length)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      runAt(cursor)
    }
  }

  return (
    <div
      className="fixed inset-0 z-20 flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
      // 바깥을 누르면 닫힌다. 팔레트는 잠깐 쓰는 것이라 확인을 묻지 않는다.
      onMouseDown={onClose}
    >
      <Card
        role="dialog"
        aria-modal="true"
        aria-label={t('common:keys.palette')}
        className="w-full max-w-lg p-0"
        onMouseDown={(event) => {
          event.stopPropagation()
        }}
      >
        <input
          autoFocus
          type="text"
          role="combobox"
          aria-expanded
          aria-controls={listId}
          aria-activedescendant={shown[cursor] ? `${listId}-${shown[cursor].id}` : undefined}
          aria-label={t('common:keys.palettePlaceholder')}
          placeholder={t('common:keys.palettePlaceholder')}
          className="w-full border-b border-border bg-transparent px-4 py-3 text-sm text-fg outline-none placeholder:text-muted"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setAt(0)
          }}
          onKeyDown={onKeyDown}
        />

        {shown.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted">{t('common:state.empty')}</p>
        ) : (
          <ul id={listId} role="listbox" className="max-h-80 overflow-auto py-1">
            {shown.map((command, index) => (
              <li
                key={command.id}
                id={`${listId}-${command.id}`}
                role="option"
                aria-selected={index === cursor}
                className={
                  index === cursor
                    ? 'flex cursor-pointer items-center justify-between gap-3 bg-accent/10 px-4 py-2 text-sm text-fg'
                    : 'flex cursor-pointer items-center justify-between gap-3 px-4 py-2 text-sm text-fg'
                }
                onMouseEnter={() => {
                  setAt(index)
                }}
                onClick={() => {
                  runAt(index)
                }}
              >
                <span>{command.label}</span>
                <span className="shrink-0 text-xs text-muted">{command.kind}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
