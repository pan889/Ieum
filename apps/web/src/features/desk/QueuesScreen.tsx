/**
 * 큐 — 상담원의 작업 화면 (feature-map C3).
 *
 * 큐는 **뷰다.** 조건(IQL)을 저장하고 목록은 볼 때마다 만든다. 티켓을 큐에
 * 옮겨 담는 손잡이가 없는 것이 의도다: 사람이 옮겨야 하는 큐는 반드시 실제
 * 상태와 어긋난다.
 *
 * 조건 입력은 이슈 검색과 **같은 편집기**를 쓴다. 자동완성과 오류 위치
 * 표시가 이미 거기 있고, 큐 조건을 다른 입력창으로 두면 같은 문법을 두 방식으로
 * 배워야 한다.
 *
 * 큐가 아무 것도 못 찾는 것과 **조건이 틀린 것**을 화면이 구별해서 말한다.
 * 둘을 "결과 없음" 으로 뭉치면, 조건을 잘못 적은 관리자는 티켓이 없다고
 * 믿는다 — 그 사이 그 큐로 들어와야 할 요청은 아무도 안 본다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Project, Queue } from '@ieum/api-client'

import { IqlEditor } from '@/features/issues/IqlEditor'
import { categoryTone, formatDateTime, priorityLabel } from '@/features/issues/format'
import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

import { CannedResponses } from './CannedResponses'

const EMPTY = { name: '', iql: '' }

export function QueuesScreen() {
  const { t } = useTranslation(['desk', 'issues', 'common'])
  const queryClient = useQueryClient()
  const [picked, setPicked] = useState<Project | null>(null)
  const [openQueue, setOpenQueue] = useState<string | null>(null)
  const [editing, setEditing] = useState<Queue | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)

  // 포털 화면과 같은 판단: 첫 프로젝트를 기본값으로 쓰되 고르는 길은 검색이다
  // (드롭다운에 목록을 통째로 넣으면 상한 뒤의 프로젝트를 못 고른다).
  const first = useQuery({
    queryKey: ['projects', 'first'],
    queryFn: () => projectsApi.list({ limit: 1 }),
  })
  const project = picked ?? first.data?.items[0] ?? null
  const projectId = project?.id ?? ''

  const queues = useQuery({
    queryKey: ['queues', projectId],
    queryFn: () => deskApi.listQueues(projectId),
    enabled: projectId.length > 0,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['queues', projectId] })

  const save = useMutation({
    mutationFn: () =>
      editing
        ? deskApi.updateQueue(editing.id, { name: form.name, iql: form.iql })
        : deskApi.createQueue({
            project_id: projectId,
            name: form.name,
            iql: form.iql,
            position: queues.data?.length ?? 0,
          }),
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY)
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteQueue(id),
    onSuccess: async () => {
      setOpenQueue(null)
      await refresh()
    },
  })

  const rows = queues.data ?? []
  const open = rows.find((queue) => queue.id === openQueue) ?? rows[0] ?? null

  return (
    <section className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('desk:queues.title')}</h1>
        <p className="text-sm text-muted">{t('desk:queues.description')}</p>
      </header>

      <ProjectPicker label={t('desk:queues.project')} chosen={project} onPick={setPicked} />

      {queues.isError ? <Alert>{describeError(queues.error)}</Alert> : null}
      {/* 조회가 성공했을 때만 "아직 없다" 를 말한다. 403 과 나란히 그리면
          있는지 없는지 **모르는** 상태를 "없다" 로 단정하게 된다. */}
      {queues.isSuccess && rows.length === 0 && !adding ? (
        <p className="text-sm text-muted">{t('desk:queues.empty')}</p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {rows.map((queue) => (
          <Button
            key={queue.id}
            type="button"
            variant={open?.id === queue.id ? 'primary' : 'ghost'}
            onClick={() => { setOpenQueue(queue.id) }}
          >
            {queue.name}
          </Button>
        ))}
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setEditing(null)
            setForm(EMPTY)
            setAdding(true)
          }}
        >
          {t('desk:queues.add')}
        </Button>
      </div>

      {adding || editing ? (
        <Card className="flex flex-col gap-3">
          <h2 className="text-sm font-medium">
            {editing ? t('desk:queues.editTitle') : t('desk:queues.addTitle')}
          </h2>
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
          <Field
            label={t('desk:queues.name')}
            value={form.name}
            onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
          />
          <IqlEditor
            label={t('desk:queues.condition')}
            placeholder="status != Closed AND priority >= 3"
            value={form.iql}
            onChange={(next) => { setForm({ ...form, iql: next }) }}
            onRun={() => { save.mutate() }}
          />
          <p className="text-xs text-muted">{t('desk:queues.conditionHint')}</p>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              loading={save.isPending}
              disabled={form.name.trim() === '' || form.iql.trim() === ''}
              onClick={() => { save.mutate() }}
            >
              {t('common:action.save')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setAdding(false)
                setEditing(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
          </div>
        </Card>
      ) : null}

      {open ? (
        <QueueTickets
          queue={open}
          onEdit={() => {
            setAdding(false)
            setEditing(open)
            setForm({ name: open.name, iql: open.iql })
          }}
          onDelete={() => { remove.mutate(open.id) }}
          deleting={remove.isPending}
          deleteError={remove.isError ? describeError(remove.error) : null}
        />
      ) : null}

      {projectId.length > 0 ? <CannedResponses projectId={projectId} /> : null}
    </section>
  )
}

function QueueTickets({
  queue,
  onEdit,
  onDelete,
  deleting,
  deleteError,
}: {
  queue: Queue
  onEdit: () => void
  onDelete: () => void
  deleting: boolean
  deleteError: string | null
}) {
  const { t } = useTranslation(['desk', 'issues', 'common'])
  const tickets = useQuery({
    queryKey: ['queues', 'tickets', queue.id],
    queryFn: () => deskApi.runQueue(queue.id, { limit: 50 }),
  })

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-medium">{queue.name}</h2>
          {/* 조건을 숨기지 않는다. 목록이 뜻밖일 때 사람이 볼 것은 조건이다. */}
          <code className="text-xs text-muted">{queue.iql}</code>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="ghost" className="text-xs" onClick={onEdit}>
            {t('common:action.edit')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="text-xs"
            loading={deleting}
            onClick={onDelete}
          >
            {t('common:action.delete')}
          </Button>
        </div>
      </div>

      {deleteError ? <Alert>{deleteError}</Alert> : null}
      {/* **조건이 틀린 것과 결과가 없는 것을 구별한다.** 둘을 "결과 없음" 으로
          뭉치면 조건을 잘못 적은 사람은 티켓이 없다고 믿는다. */}
      {tickets.isError ? (
        <Alert>
          {t('desk:queues.conditionFailed')} {describeError(tickets.error)}
        </Alert>
      ) : null}
      {tickets.isSuccess && tickets.data.items.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:queues.noTickets')}</p>
      ) : null}

      <ul className="flex flex-col divide-y divide-border">
        {(tickets.data?.items ?? []).map((ticket) => (
          <li key={ticket.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            {/* 타입 있는 라우트다. 주소를 문자열로 조립하면 `tsc` 가
                거절한다 — 라우트를 지우거나 이름을 바꿀 때 깨진 링크가
                조용히 남지 않게 하는 장치다. */}
            <Link
              to="/issues/$issueKey"
              params={{ issueKey: ticket.key }}
              className="font-mono text-xs text-accent underline"
            >
              {ticket.key}
            </Link>
            <span className="min-w-0 flex-1 truncate">{ticket.summary}</span>
            <Badge tone={categoryTone(ticket.state_category)}>{ticket.state_name}</Badge>
            {/* `priorityLabel` 은 키가 아니라 이미 번역된 문구를 준다.
                `t()` 로 한 번 더 감싸면 문구가 키로 취급돼 그대로 찍힌다. */}
            <span className="text-xs text-muted">{priorityLabel(ticket.priority)}</span>
            <span className="text-xs text-muted">{formatDateTime(ticket.updated_at)}</span>
          </li>
        ))}
      </ul>

      {tickets.data?.next_cursor ? (
        <p className="text-xs text-muted">{t('desk:queues.truncated')}</p>
      ) : null}
    </Card>
  )
}
