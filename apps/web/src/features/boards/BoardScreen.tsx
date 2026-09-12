import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import type {
  Board,
  BoardCard,
  BoardColumnContent,
  BoardSwimlane,
  SprintMode,
} from '@ieum/api-client'
import { SWIMLANE_FIELDS } from '@ieum/api-client'
import clsx from 'clsx'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { priorityLabel } from '@/features/issues/format'
import { useUserNames, useWorkflowStates } from '@/features/issues/hooks'
import { boardsApi, issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Select } from '@/shared/ui/primitives'

import { columnTarget, stateFitsColumn } from './columnTarget'

interface PendingMove {
  card: BoardCard
  /**
   * 어느 컬럼으로 떨어뜨렸는지. null 이면 카드의 "상태 변경" 버튼으로 열린
   * 것이다 — 그때는 목적지가 없으니 **가능한 전이 전부**를 보여야 한다.
   * 카드가 지금 있는 컬럼을 목적지로 넣으면 제자리 전이만 남는다.
   *
   * 스윔레인이 있어도 컬럼 순서는 레인마다 같으므로 인덱스 하나면 된다.
   */
  columnIndex: number | null
}

export function BoardScreen() {
  const { boardId } = useParams({ from: '/boards/$boardId' })
  const { t } = useTranslation(['boards', 'common', 'issues', 'sprints'])
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
      // **다시 받아 올 때까지 기다린다.** 안 기다리면 옮기기가 끝나는 순간
      // 단추가 다시 눌리는데, 화면의 카드는 아직 **옛 `version`** 을 들고
      // 있다. 그 값으로 다음 이동을 보내면 서버가 "누가 먼저 고쳤다"(409)
      // 로 막는다 — 아무도 안 고쳤는데.
      return queryClient.invalidateQueries({ queryKey: ['boards', 'content', boardId] })
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
        <span className="ml-auto flex items-start gap-4">
          <SprintModePicker board={board} />
          <SwimlanePicker board={board} />
        </span>
      </header>

      {/*
        **무엇으로 걸렀는지 말한다.** 보드가 이번 스프린트만 보여 주는데
        그 사실을 안 적으면, 백로그에 쌓인 일이 안 보이는 것과 아예 없는
        것을 구분할 수 없다. 도는 스프린트가 없어서 안 거른 경우도 같다.
      */}
      {board.sprint_mode === 'active' ? (
        <p className="text-xs text-muted">
          {content.data.sprint === null
            ? t('sprints:board.noActive')
            : t('sprints:board.filtered', { name: content.data.sprint.name })}
        </p>
      ) : null}

      {/* 대화상자가 떠 있으면 그 안에서 말한다. 여기 띄우면 덮개(`inset-0`)에
          가려서 **아무 말도 없이 실패한 것처럼 보인다.** */}
      {moveError !== null && pending === null ? <Alert>{moveError}</Alert> : null}

      {content.data.lanes.map((lane) => (
        <Lane
          key={lane.key}
          lane={lane}
          board={board}
          // 스윔레인이 없으면 레인이 하나뿐이고 머리글도 필요 없다.
          showHeader={content.data.lanes.length > 1 || lane.key !== ''}
          isDropTarget={dragging !== null}
          onDragStartCard={setDragging}
          onDropIn={(index) => { if (dragging) drop(index, dragging) }}
          onPickMove={(card) => { setPending({ card, columnIndex: null }); }}
        />
      ))}

      {pending ? (
        <MoveDialog
          card={pending.card}
          column={
            pending.columnIndex === null
              ? null
              : // 컬럼 정의는 레인마다 같으니 첫 레인에서 꺼내면 된다.
                (content.data.lanes[0]?.columns[pending.columnIndex] ?? null)
          }
          stateById={stateById}
          pending={move.isPending}
          error={moveError}
          onCancel={() => { setPending(null); setMoveError(null) }}
          onPick={(transitionId) => { move.mutate({ card: pending.card, transitionId }); }}
        />
      ) : null}
    </section>
  )
}

/**
 * 보드가 스프린트를 볼지 말지.
 *
 * 스윔레인과 같은 자리의 설정이다 — 보드 정의라 **모두**에게 적용된다.
 */
function SprintModePicker({ board }: { board: Board }) {
  const { t } = useTranslation(['sprints'])
  const queryClient = useQueryClient()

  const change = useMutation({
    mutationFn: (mode: string) =>
      boardsApi.update(board.id, { sprint_mode: mode as SprintMode }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['boards', 'content', board.id] }),
  })

  return (
    <div className="flex flex-col items-end gap-1">
      <Select
        label={t('sprints:board.mode')}
        className="py-1 text-xs"
        value={board.sprint_mode}
        onChange={(e) => { change.mutate(e.target.value) }}
      >
        <option value="active">{t('sprints:board.mode.active')}</option>
        <option value="all">{t('sprints:board.mode.all')}</option>
      </Select>
      {change.isError ? <Alert>{describeError(change.error)}</Alert> : null}
    </div>
  )
}

/**
 * 스윔레인 기준 선택.
 *
 * 보드 설정이라 바꾸면 그 보드를 보는 **모두**에게 적용된다(`board.swimlane_by`).
 * 보드 관리 권한이 필요하므로 없는 사람에게는 서버가 거절하고, 그 사실을
 * 그대로 보여 준다 — 조용히 되돌아가면 눌린 게 안 눌린 줄 안다.
 */
function SwimlanePicker({ board, className }: { board: Board; className?: string }) {
  const { t } = useTranslation(['boards'])
  const queryClient = useQueryClient()

  const change = useMutation({
    mutationFn: (field: string) =>
      boardsApi.update(
        board.id,
        field === ''
          ? { clear_swimlane: true }
          : { swimlane_by: field as (typeof SWIMLANE_FIELDS)[number] },
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['boards', 'content', board.id] }),
  })

  return (
    <div className={clsx('flex flex-col items-end gap-1', className)}>
      <Select
        label={t('boards:swimlane.label')}
        className="py-1 text-xs"
        value={board.swimlane_by ?? ''}
        onChange={(e) => { change.mutate(e.target.value) }}
      >
        <option value="">{t('boards:swimlane.none')}</option>
        {SWIMLANE_FIELDS.map((field) => (
          <option key={field} value={field}>{t(`boards:swimlane.${field}`)}</option>
        ))}
      </Select>
      {change.isError ? <Alert>{describeError(change.error)}</Alert> : null}
    </div>
  )
}

/** 스윔레인 한 줄. 컬럼은 레인마다 같은 순서로 반복된다. */
function Lane({
  lane,
  board,
  showHeader,
  isDropTarget,
  onDragStartCard,
  onDropIn,
  onPickMove,
}: {
  lane: BoardSwimlane
  board: Board
  showHeader: boolean
  isDropTarget: boolean
  onDragStartCard: (card: BoardCard) => void
  onDropIn: (columnIndex: number) => void
  onPickMove: (card: BoardCard) => void
}) {
  const count = lane.columns.reduce((sum, column) => sum + column.issues.length, 0)

  return (
    <div className="flex flex-col gap-2">
      {showHeader ? <LaneHeader lane={lane} field={board.swimlane_by} count={count} /> : null}
      <div className="flex gap-3 overflow-x-auto pb-2">
        {lane.columns.map((column, index) => (
          <Column
            key={column.name}
            column={column}
            isDropTarget={isDropTarget}
            onDragStartCard={onDragStartCard}
            onDrop={() => { onDropIn(index) }}
            onPickMove={onPickMove}
          />
        ))}
      </div>
    </div>
  )
}

function LaneHeader({
  lane,
  field,
  count,
}: {
  lane: BoardSwimlane
  field: string | null
  count: number
}) {
  const { t } = useTranslation(['boards', 'issues'])
  // 우선순위 이름은 서버가 번역하지 않는다. 담당자 없음도 여기서 붙인다.
  const label =
    field === 'priority'
      ? priorityLabel(Number(lane.key))
      : lane.key === 'none'
        ? t('issues:detail.unassigned')
        : lane.label

  return (
    <h2 className="flex items-baseline gap-2 border-b border-border pb-1 text-sm font-medium">
      {label}
      <span className="text-xs font-normal text-muted">{count}</span>
    </h2>
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
          {/* 눈에 보이는 장수를 센다. 스윔레인이 있으면 `loaded`(컬럼 전체)
              와 다르고, 1장만 보이는데 "3장" 이라고 하면 틀린 말이다.
              WIP 판정은 그대로 컬럼 전체 기준이다. */}
          {column.truncated
            ? t('boards:column.truncated', { count: column.issues.length })
            : t('boards:column.count', { count: column.issues.length })}
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
  error,
  onCancel,
  onPick,
}: {
  card: BoardCard
  /** 떨어뜨린 컬럼. null 이면 목적지가 정해지지 않은 호출이다. */
  column: BoardColumnContent | null
  stateById: Map<string, { id: string; name: string; category: string }>
  pending: boolean
  /** 옮기다 실패한 이유. **여기서 말한다** — 뒤쪽 알림은 이 덮개에 가린다. */
  error: string | null
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

        {error !== null ? (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        ) : null}

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
