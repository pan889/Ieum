import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { IssueSummary, Project, Sprint, SprintState } from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { searchApi, sprintsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  PageHeader,
  Select,
} from '@/shared/ui/primitives'

import { BurndownChart } from './BurndownChart'
import { CloseSprintForm } from './CloseSprintForm'
import { inBacklog, inSprint } from './iql'

const TONES: Record<SprintState, 'neutral' | 'in_progress' | 'done'> = {
  future: 'neutral',
  active: 'in_progress',
  closed: 'done',
}

/** `datetime-local` 값 → ISO. 비어 있으면 `null` 이다("정하지 않았다"). */
function toIso(value: string): string | null {
  return value === '' ? null : new Date(value).toISOString()
}

export function SprintsScreen() {
  const { t, i18n } = useTranslation(['sprints', 'common', 'issues'])
  const queryClient = useQueryClient()
  const [project, setProject] = useState<Project | null>(null)
  const projectId = project?.id ?? null

  const [name, setName] = useState('')
  const [goal, setGoal] = useState('')
  const [startsAt, setStartsAt] = useState('')
  const [endsAt, setEndsAt] = useState('')
  const [closing, setClosing] = useState<Sprint | null>(null)
  const [opened, setOpened] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [target, setTarget] = useState('')

  const listKey = ['sprints', 'list', projectId]
  const sprints = useQuery({
    queryKey: listKey,
    queryFn: () => sprintsApi.list(projectId as string),
    enabled: projectId !== null,
  })

  const backlog = useQuery({
    queryKey: ['sprints', 'backlog', project?.key],
    queryFn: () => searchApi.search({ iql: inBacklog(project?.key ?? ''), limit: 50 }),
    enabled: project !== null,
  })

  const burndown = useQuery({
    queryKey: ['sprints', 'burndown', opened],
    queryFn: () => sprintsApi.burndown(opened as string),
    enabled: opened !== null,
  })

  const contents = useQuery({
    queryKey: ['sprints', 'contents', opened, project?.key],
    queryFn: () => {
      const sprint = sprints.data?.find((row) => row.id === opened)
      return searchApi.search({
        iql: inSprint(project?.key ?? '', sprint?.name ?? ''),
        limit: 50,
      })
    },
    enabled: opened !== null && project !== null && sprints.data !== undefined,
  })

  /** 목록·백로그·번다운이 한 번에 어긋나지 않게 같이 새로 받는다. */
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sprints'] }),
      queryClient.invalidateQueries({ queryKey: ['boards'] }),
    ])
  }

  const create = useMutation({
    mutationFn: () =>
      sprintsApi.create({
        project_id: projectId as string,
        name,
        goal: goal.trim() === '' ? null : goal,
        starts_at: toIso(startsAt),
        ends_at: toIso(endsAt),
      }),
    onSuccess: async () => {
      setName('')
      setGoal('')
      setStartsAt('')
      setEndsAt('')
      await refresh()
    },
  })

  const start = useMutation({
    mutationFn: (id: string) => sprintsApi.start(id),
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: (id: string) => sprintsApi.remove(id),
    onSuccess: async (_result, id) => {
      if (opened === id) setOpened(null)
      await refresh()
    },
  })

  /** 한 건을 백로그로. 여러 건 고르는 것은 백로그 쪽 일이라 여기선 한 건씩이다. */
  const pull = useMutation({
    mutationFn: (issueId: string) =>
      sprintsApi.assign({
        project_id: projectId as string,
        sprint_id: null,
        issue_ids: [issueId],
      }),
    onSuccess: refresh,
  })

  const assign = useMutation({
    mutationFn: (sprintId: string | null) =>
      sprintsApi.assign({
        project_id: projectId as string,
        sprint_id: sprintId,
        issue_ids: picked,
      }),
    onSuccess: async () => {
      setPicked([])
      await refresh()
    },
  })

  const dates = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' })
  const day = (iso: string | null) => (iso === null ? null : dates.format(new Date(iso)))
  const window = (row: Sprint) => {
    const starts = day(row.starts_at)
    const ends = day(row.ends_at)
    return starts !== null && ends !== null
      ? t('sprints:list.window', { starts, ends })
      : t('sprints:list.noWindow')
  }

  /** 옮겨 받을 수 있는 스프린트 — 닫히지 않았고 자기 자신이 아닌 것. */
  const targets = (row: Sprint) =>
    (sprints.data ?? []).filter((s) => s.id !== row.id && s.state !== 'closed')

  const openSprint = sprints.data?.find((row) => row.id === opened) ?? null

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <PageHeader title={t('sprints:title')} />

      <ProjectPicker label={t('issues:list.project')} chosen={project} onPick={setProject} />

      {/* **빈 화면을 그냥 비워 두지 않는다.** 프로젝트를 고르기 전의 이
          화면은 제목 한 줄과 고르는 줄만 남은 백지였고, 처음 온 사람은
          고장인지 아직 안 고른 것인지 알 수 없다(`BoardsScreen` 과 같다). */}
      {projectId === null ? (
        <EmptyState
          title={t('common:state.pickProject')}
          description={t('common:state.pickProjectHint')}
        />
      ) : sprints.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : sprints.isError ? (
        <Alert>{describeError(sprints.error)}</Alert>
      ) : sprints.data.length === 0 ? (
        <EmptyState
          title={t('sprints:list.empty')}
          description={t('sprints:list.emptyHint')}
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {sprints.data.map((row) => (
            <li key={row.id}>
              <Card className="flex flex-col gap-2 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    className="font-medium hover:underline"
                    aria-expanded={opened === row.id}
                    onClick={() => { setOpened(opened === row.id ? null : row.id) }}
                  >
                    {row.name}
                  </button>
                  <Badge tone={TONES[row.state]}>{t(`sprints:state.${row.state}`)}</Badge>
                  <span className="text-xs text-muted">
                    {t('sprints:list.totals', {
                      issues: row.issues,
                      remaining: row.remaining_issues,
                    })}
                  </span>
                  <span className="text-xs text-muted">{window(row)}</span>
                  <span className="ml-auto flex gap-2">
                    {row.state === 'future' ? (
                      <Button
                        variant="ghost"
                        loading={start.isPending && start.variables === row.id}
                        onClick={() => { start.mutate(row.id) }}
                      >
                        {t('sprints:action.start')}
                      </Button>
                    ) : null}
                    {row.state === 'active' ? (
                      <Button variant="ghost" onClick={() => { setClosing(row) }}>
                        {t('sprints:action.close')}
                      </Button>
                    ) : null}
                    {row.state === 'active' ? null : (
                      <Button
                        variant="ghost"
                        title={t('sprints:action.deleteConfirm')}
                        loading={remove.isPending && remove.variables === row.id}
                        onClick={() => { remove.mutate(row.id) }}
                      >
                        {t('sprints:action.delete')}
                      </Button>
                    )}
                  </span>
                </div>
                {row.goal === null ? null : <p className="text-sm text-muted">{row.goal}</p>}
              </Card>
            </li>
          ))}
        </ul>
      )}

      {start.isError ? <Alert>{describeError(start.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {closing === null ? null : (
        <CloseSprintForm
          sprint={closing}
          targets={targets(closing)}
          onDone={async () => {
            setClosing(null)
            await refresh()
          }}
          onCancel={() => { setClosing(null) }}
        />
      )}

      {openSprint === null ? null : (
        <Card className="flex flex-col gap-4">
          <h2 className="text-sm font-medium text-muted">
            {t('sprints:contents.title', { name: openSprint.name })}
          </h2>
          {contents.isPending ? (
            <p className="text-sm text-muted">{t('common:state.loading')}</p>
          ) : contents.isError ? (
            <Alert>{describeError(contents.error)}</Alert>
          ) : contents.data.items.length === 0 ? (
            <p className="text-sm text-muted">{t('sprints:contents.empty')}</p>
          ) : (
            <IssueList
              items={contents.data.items}
              // 넣는 길만 있으면 잘못 넣은 것을 되돌릴 수 없다.
              onRemove={
                openSprint.state === 'closed'
                  ? undefined
                  : (id) => { pull.mutate(id) }
              }
              removeLabel={t('sprints:backlog.remove')}
              busy={pull.isPending ? pull.variables : null}
            />
          )}
          {pull.isError ? <Alert>{describeError(pull.error)}</Alert> : null}

          <h2 className="text-sm font-semibold text-fg">{t('sprints:burndown.title')}</h2>
          {burndown.isPending ? (
            <p className="text-sm text-muted">{t('common:state.loading')}</p>
          ) : burndown.isError ? (
            <Alert>{describeError(burndown.error)}</Alert>
          ) : (
            <BurndownChart
              points={burndown.data}
              endsOn={openSprint.ends_at?.slice(0, 10) ?? undefined}
            />
          )}
        </Card>
      )}

      {project === null ? null : (
        <Card className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-fg">{t('sprints:backlog.title')}</h2>
          {backlog.isPending ? (
            <p className="text-sm text-muted">{t('common:state.loading')}</p>
          ) : backlog.isError ? (
            <Alert>{describeError(backlog.error)}</Alert>
          ) : backlog.data.items.length === 0 ? (
            <p className="text-sm text-muted">{t('sprints:backlog.empty')}</p>
          ) : (
            <>
              <ul className="flex flex-col gap-1">
                {backlog.data.items.map((issue) => (
                  <li key={issue.id} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      label={`${issue.key} ${issue.summary}`}
                      checked={picked.includes(issue.id)}
                      onChange={(event) => {
                        setPicked((was) =>
                          event.target.checked
                            ? [...was, issue.id]
                            : was.filter((id) => id !== issue.id),
                        )
                      }}
                    />
                  </li>
                ))}
              </ul>
              {assign.isError ? <Alert>{describeError(assign.error)}</Alert> : null}
              <div className="flex flex-wrap items-end gap-3">
                <Select
                  label={t('sprints:backlog.pickSprint')}
                  value={target}
                  onChange={(event) => { setTarget(event.target.value) }}
                >
                  <option value="">—</option>
                  {(sprints.data ?? [])
                    .filter((row) => row.state !== 'closed')
                    .map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name}
                      </option>
                    ))}
                </Select>
                <Button
                  loading={assign.isPending}
                  disabled={picked.length === 0 || target === ''}
                  onClick={() => { assign.mutate(target) }}
                >
                  {t('sprints:backlog.add')}
                </Button>
                <span className="text-xs text-muted">
                  {t('sprints:backlog.selected', { count: picked.length })}
                </span>
              </div>
            </>
          )}
        </Card>
      )}

      {projectId === null ? null : (
        <Card>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => { event.preventDefault(); create.mutate() }}
          >
            <h2 className="text-sm font-semibold text-fg">{t('sprints:create.title')}</h2>
            {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
            <Field
              label={t('sprints:create.name')}
              required
              value={name}
              onChange={(e) => { setName(e.target.value) }}
            />
            <Field
              label={t('sprints:create.goal')}
              hint={t('sprints:create.goalHint')}
              value={goal}
              onChange={(e) => { setGoal(e.target.value) }}
            />
            <div className="flex flex-wrap gap-3">
              <Field
                label={t('sprints:create.startsAt')}
                type="datetime-local"
                value={startsAt}
                onChange={(e) => { setStartsAt(e.target.value) }}
              />
              <Field
                label={t('sprints:create.endsAt')}
                type="datetime-local"
                value={endsAt}
                onChange={(e) => { setEndsAt(e.target.value) }}
              />
            </div>
            <Button type="submit" loading={create.isPending} disabled={name.trim() === ''}>
              {t('sprints:create.submit')}
            </Button>
          </form>
        </Card>
      )}
    </section>
  )
}

function IssueList({
  items,
  onRemove,
  removeLabel,
  busy,
}: {
  items: IssueSummary[]
  onRemove?: ((issueId: string) => void) | undefined
  removeLabel?: string
  busy?: string | null
}) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {items.map((issue) => (
        <li key={issue.id} className="flex items-center gap-2">
          <Link
            to="/issues/$issueKey"
            params={{ issueKey: issue.key }}
            className="font-mono text-xs text-accent hover:underline"
          >
            {issue.key}
          </Link>
          <span className="truncate">{issue.summary}</span>
          <Badge tone={issue.state_category as 'todo' | 'in_progress' | 'done'} className="ml-auto">
            {issue.state_name}
          </Badge>
          {onRemove === undefined ? null : (
            <Button
              variant="ghost"
              className="py-0.5 text-xs"
              loading={busy === issue.id}
              aria-label={`${removeLabel ?? ''} ${issue.key}`}
              onClick={() => { onRemove(issue.id) }}
            >
              {removeLabel}
            </Button>
          )}
        </li>
      ))}
    </ul>
  )
}
