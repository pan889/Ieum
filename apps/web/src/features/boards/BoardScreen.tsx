import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import type { BoardCard, BoardColumnContent } from '@ieum/api-client'
import clsx from 'clsx'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { priorityLabel } from '@/features/issues/format'
import { useUserNames, useWorkflowStates } from '@/features/issues/hooks'
import { boardsApi, issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card } from '@/shared/ui/primitives'

import { columnTarget, stateFitsColumn } from './columnTarget'

interface PendingMove {
  card: BoardCard
  /**
   * 어느 컬럼으로 떨어뜨렸는지. null 이면 카드의 "상태 변경" 버튼으로 열린
   * 것이다 — 그때는 목적지가 없으니 **가능한 전이 전부**를 보여야 한다.
   * 카드가 지금 있는 컬럼을 목적지로 넣으면 제자리 전이만 남는다.
   */
  columnIndex: number | null
}

export function BoardScreen() {
  const { boardId } = useParams({ from: '/boards/$boardId' })
  const { t } = useTranslation(['boards', 'common', 'issues'])
  const queryClient = useQueryClient()

  const content = useQuery({
    queryKey: ['boards', 'content', boardId],
    queryFn: () => boardsApi.content(boardId),
  })

  const projectId = content.data?.board.project_id ?? null
  const states = useWorkflowStates(projectId)
  const stateById = useMemo(
    () => new Map((states.data ?? []).map((s) => [s.id, s])),
    [states.data],
  )

  const [dragging, setDragging] = useState<BoardCard | null>(null)
  const [pending, setPending] = useState<PendingMove | null>(null)
  const [moveError, setMoveError] = useState<string | null>(null)

  const move = useMutation({
    mutationFn: (input: { card: BoardCard; transitionId: string }) =>
      boardsApi.move(
        boardId,
        { issue_id: input.card.id, transition_id: input.transitionId },
        input.card.version,
      ),
    onSuccess: () => {
      setPending(null)
      setMoveError(null)
      void queryClient.invalidateQueries({ queryKey: ['boards', 'content', boardId] })
    },
    onError: (error) => { setMoveError(describeError(error)); },
  })

  if (content.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (content.isError) {
    return <Alert>{describeError(content.error)}</Alert>
  }

  const board = content.data.board

  const drop = (columnIndex: number, card: BoardCard) => {
    setDragging(null)
    setMoveError(null)
    // 어디로 옮길지는 전이가 정한다. 컬럼은 IQL 이라 목적 상태가 유일하지
    // 않을 수 있다 — 그럴 땐 묻는다 (columnTarget 참고).
    setPending({ card, columnIndex })
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-baseline gap-3">
        <Link to="/boards" className="text-sm text-accent hover:underline">
          {t('boards:list.title')}
        </Link>
        <h1 className="text-xl font-semibold">{board.name}</h1>
      </header>

      {moveError ? <Alert>{moveError}</Alert> : null}

      <div className="flex gap-3 overflow-x-auto pb-2">
        {content.data.columns.map((column, index) => (
          <Column
            key={column.name}
            column={column}
            isDropTarget={dragging !== null}
            onDragStartCard={setDragging}
            onDrop={() => { if (dragging) drop(index, dragging) }}
            onPickMove={(card) => { setPending({ card, columnIndex: null }); }}
          />
        ))}
      </div>

      {pending ? (
        <MoveDialog
          card={pending.card}
          column={
            pending.columnIndex === null
              ? null
              : (content.data.columns[pending.columnIndex] as BoardColumnContent)
          }
          stateById={stateById}
          pending={move.isPending}
          onCancel={() => { setPending(null); }}
          onPick={(transitionId) => { move.mutate({ card: pending.card, transitionId }); }}
        />
      ) : null}
    </section>
  )
}

function Column({
  column,
  isDropTarget,
  onDragStartCard,
  onDrop,
  onPickMove,
}: {
  column: BoardColumnContent
  isDropTarget: boolean
  onDragStartCard: (card: BoardCard) => void
  onDrop: () => void
  onPickMove: (card: BoardCard) => void
}) {
  const { t } = useTranslation(['boards', 'issues'])
  const [over, setOver] = useState(false)
  const names = useUserNames(column.issues.map((i) => i.assignee_id))

  return (
    <div
      className={clsx(
        'flex w-72 shrink-0 flex-col rounded-card border bg-surface',
        over ? 'border-accent' : 'border-border',
      )}
      onDragOver={(e) => {
        if (!isDropTarget) return
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => { setOver(false); }}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        onDrop()
      }}
    >
      <div className="flex items-baseline gap-2 border-b border-border px-3 py-2">
        <h2 className="text-sm font-medium">{column.name}</h2>
        <span
          className={clsx('text-xs', column.over_wip ? 'font-medium text-danger' : 'text-muted')}
          title={column.over_wip ? t('boards:column.overWip') : undefined}
        >
          {column.truncated
            ? t('boards:column.truncated', { count: column.loaded })
            : t('boards:column.count', { count: column.loaded })}
          {column.wip_limit !== null
            ? ` · ${t('boards:column.wip', { limit: column.wip_limit })}`
            : ''}
        </span>
      </div>

      <ul className="flex min-h-24 flex-col gap-2 p-2">
        {column.issues.length === 0 ? (
          <li className="px-1 py-4 text-center text-xs text-muted">
            {over ? t('boards:card.dropHere') : t('boards:column.empty')}
          </li>
        ) : (
          column.issues.map((card) => (
            <li key={card.id}>
              <Card
                draggable
                onDragStart={() => { onDragStartCard(card); }}
                className="flex cursor-grab flex-col gap-1.5 p-3"
              >
                <div className="flex items-baseline gap-2">
                  <Link
                    to="/issues/$issueKey"
                    params={{ issueKey: card.key }}
                    className="font-mono text-xs text-accent hover:underline"
                  >
                    {card.key}
                  </Link>
                  <span className="ml-auto text-xs text-muted">
                    {priorityLabel(card.priority)}
                  </span>
                </div>
                <p className="text-sm">{card.summary}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge>{card.type_name}</Badge>
                  {card.labels.map((label) => (
                    <Badge key={label}>{label}</Badge>
                  ))}
                  <span className="ml-auto text-xs text-muted">
                    {card.assignee_id
                      ? (names.data?.get(card.assignee_id) ?? '…')
                      : t('issues:detail.unassigned')}
                  </span>
                </div>
                {/* 네이티브 드래그는 키보드로 못 쓴다. 같은 일을 하는 버튼을 함께 둔다. */}
                <Button
                  variant="ghost"
                  className="justify-start px-1 py-0.5 text-xs"
                  onClick={() => { onPickMove(card); }}
                >
                  {t('issues:transition.title')}
                </Button>
              </Card>
            </li>
          ))
        )}
      </ul>
    </div>
  )
}

function MoveDialog({
  card,
  column,
  stateById,
  pending,
  onCancel,
  onPick,
}: {
  card: BoardCard
  /** 떨어뜨린 컬럼. null 이면 목적지가 정해지지 않은 호출이다. */
  column: BoardColumnContent | null
  stateById: Map<string, { id: string; name: string; category: string }>
  pending: boolean
  onCancel: () => void
  onPick: (transitionId: string) => void
}) {
  const { t } = useTranslation(['boards', 'issues', 'common'])
  const transitions = useQuery({
    queryKey: ['issues', 'transitions', card.id, card.version],
    queryFn: () => issuesApi.transitions(card.id),
  })

  const target = column === null ? null : columnTarget(column.iql)
  const available = (transitions.data ?? []).filter((tr) => tr.blocked_by.length === 0)
  const fitting = available.filter((tr) => stateFitsColumn(target, stateById.get(tr.to_state_id)))
  // 목적지가 있고 그리로 가는 전이를 알아냈으면 그것만 보여준다. 아니면 전부.
  const choices = fitting.length > 0 ? fitting : available

  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <Card
        role="dialog"
        aria-modal="true"
        aria-label={t('issues:transition.title')}
        className="w-full max-w-sm"
      >
        <h2 className="text-sm font-medium">
          {column === null ? card.key : `${card.key} → ${column.name}`}
        </h2>

        {transitions.isPending ? (
          <p className="mt-3 text-sm text-muted">{t('common:state.loading')}</p>
        ) : choices.length === 0 ? (
          <p className="mt-3 text-sm text-muted">{t('issues:transition.none')}</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {choices.map((transition) => (
              <li key={transition.id}>
                <Button
                  variant="secondary"
                  className="w-full justify-start"
                  loading={pending}
                  onClick={() => { onPick(transition.id); }}
                >
                  {transition.name} → {transition.to_state_name}
                </Button>
              </li>
            ))}
          </ul>
        )}

        {(transitions.data ?? []).some((tr) => tr.blocked_by.length > 0) ? (
          <ul className="mt-3 flex flex-col gap-1 text-xs text-muted">
            {(transitions.data ?? [])
              .filter((tr) => tr.blocked_by.length > 0)
              .map((tr) => (
                <li key={tr.id}>
                  {tr.name} —{' '}
                  {t('issues:transition.blocked', { conditions: tr.blocked_by.join(', ') })}
                </li>
              ))}
          </ul>
        ) : null}

        <Button variant="ghost" className="mt-4 w-full" onClick={onCancel}>
          {t('issues:detail.cancel')}
        </Button>
      </Card>
    </div>
  )
}
