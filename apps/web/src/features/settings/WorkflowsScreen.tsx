/**
 * 워크플로우 — **읽기 전용.**
 *
 * 상태를 지우거나 초기 상태를 옮기는 일은 이미 그 상태에 있는 이슈를 어디로
 * 보낼지 정해야 하고, 전이 규칙과 "워크플로우당 초기 상태 하나" 제약을 함께
 * 지켜야 한다. 절반만 만든 편집은 없는 편집보다 나쁘다 — 그래서 손잡이를
 * 그리지 않고, 왜 없는지 적는다.
 *
 * 그래도 **보여 줘야** 한다. 상태 이름과 전이가 어디에도 안 보이면 "왜 이
 * 이슈가 저기로 못 가지" 에 답할 방법이 DB 뿐이다.
 *
 * `WORKFLOW_MANAGE` 는 step-up 대상이다. 2FA 없는 관리자는 거절을 본다.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { WorkflowDetail, WorkflowSummary } from '@ieum/api-client'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { workflowsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card } from '@/shared/ui/primitives'

export function WorkflowsScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const [open, setOpen] = useState<string | null>(null)

  const workflows = useQuery({ queryKey: ['workflows'], queryFn: () => workflowsApi.list() })
  const rows = workflows.data ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header>
        <h1 className="text-xl font-semibold">{t('admin:workflows.title')}</h1>
        <p className="mt-1 text-sm text-muted">{t('admin:workflows.description')}</p>
      </header>

      {workflows.isError ? <Alert>{describeError(workflows.error)}</Alert> : null}

      <ul className="flex flex-col gap-2" aria-label={t('admin:workflows.title')}>
        {rows.map((workflow) => (
          <li key={workflow.id}>
            <WorkflowCard
              workflow={workflow}
              expanded={open === workflow.id}
              onToggle={() => { setOpen(open === workflow.id ? null : workflow.id) }}
            />
          </li>
        ))}
      </ul>

      {!workflows.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:workflows.empty')}</p>
      ) : null}
    </section>
  )
}

function WorkflowCard({
  workflow,
  expanded,
  onToggle,
}: {
  workflow: WorkflowSummary
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation(['admin', 'common'])

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="truncate text-sm font-medium">
            {workflow.name}
            {workflow.is_builtin ? (
              <span className="ml-2 align-middle">
                <Badge tone="info">{t('admin:roles.builtin')}</Badge>
              </span>
            ) : null}
          </p>
          <p className="text-xs text-muted">
            {t('admin:workflows.counts', {
              states: workflow.state_count,
              transitions: workflow.transition_count,
            })}
            {/* 어떤 이슈 유형이 쓰는지. 고칠 때의 영향 범위이고, 안 보이면
                "이걸 건드리면 뭐가 바뀌지" 를 알 수 없다. */}
            {workflow.used_by.length > 0
              ? ` · ${t('admin:workflows.usedBy', { types: workflow.used_by.join(', ') })}`
              : ` · ${t('admin:workflows.unused')}`}
          </p>
        </div>
        <Button
          variant="ghost"
          className="ml-auto shrink-0 text-xs"
          aria-label={t('admin:workflows.showLabel', { name: workflow.name })}
          aria-expanded={expanded}
          onClick={onToggle}
        >
          {t('admin:workflows.show')}
        </Button>
      </div>

      {expanded ? <Detail id={workflow.id} /> : null}
    </Card>
  )
}

function Detail({ id }: { id: string }) {
  const { t } = useTranslation(['admin', 'common'])
  const detail = useQuery({
    queryKey: ['workflows', id],
    queryFn: () => workflowsApi.get(id),
  })

  if (detail.isError) return <Alert>{describeError(detail.error)}</Alert>
  const data: WorkflowDetail | undefined = detail.data
  if (!data) return <p className="text-sm text-muted">{t('common:state.loading')}</p>

  const names = new Map(data.states.map((state) => [state.id, state.name]))

  return (
    <div className="flex flex-col gap-4 border-t border-border pt-3">
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">{t('admin:workflows.states')}</p>
        <ol className="flex flex-wrap gap-1" aria-label={t('admin:workflows.states')}>
          {data.states.map((state) => (
            <li key={state.id}>
              {/* 카테고리로 색을 준다 — 시스템은 이름이 아니라 이것으로
                  "완료 여부" 를 판정한다. */}
              <Badge tone={state.category === 'done' ? 'done' : state.category === 'in_progress' ? 'in_progress' : 'todo'}>
                {state.name}
                {state.is_initial ? ` · ${t('admin:workflows.initial')}` : ''}
              </Badge>
            </li>
          ))}
        </ol>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">{t('admin:workflows.transitions')}</p>
        <ul className="flex flex-col gap-1" aria-label={t('admin:workflows.transitions')}>
          {data.transitions.map((transition) => (
            <li key={transition.id} className="text-sm">
              <span className="font-medium">{transition.name}</span>{' '}
              <span className="text-muted">
                {/* `from` 이 비어 있으면 어디서든 갈 수 있다. 그걸 안 적으면
                    전이 목록이 거짓말을 한다. */}
                {transition.from_state_id === null
                  ? t('admin:workflows.fromAnywhere')
                  : (names.get(transition.from_state_id) ?? transition.from_state_id)}
                {' → '}
                {names.get(transition.to_state_id) ?? transition.to_state_id}
              </span>
              {transition.conditions.length > 0 || transition.post_functions.length > 0 ? (
                <span className="ml-2 text-xs text-muted">
                  {t('admin:workflows.rules', {
                    conditions: transition.conditions.length,
                    post: transition.post_functions.length,
                  })}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
        {data.transitions.length === 0 ? (
          <p className="text-sm text-muted">{t('admin:workflows.noTransitions')}</p>
        ) : null}
      </div>

      {/* 왜 편집 손잡이가 없는지 적는다. 없는 것과 못 하는 것은 다르다. */}
      <p className="text-sm text-muted">{t('admin:workflows.readOnlyHint')}</p>
    </div>
  )
}
