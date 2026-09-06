import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useProjects } from '@/features/issues/hooks'
import { boardsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

/**
 * 새 보드의 출발점. 컬럼 이름은 **만든 시점의 언어로 저장된다** — 보드
 * 컬럼은 UI 문자열이 아니라 그 팀이 붙인 이름이기 때문이다. 이후 언어를
 * 바꿔도 이름은 그대로다.
 */
function defaultColumns(t: (key: string) => string) {
  return [
    { name: t('boards:create.defaultColumn.todo'), iql: 'statusCategory = todo', wip_limit: null },
    {
      name: t('boards:create.defaultColumn.inProgress'),
      iql: 'statusCategory = in_progress',
      wip_limit: 5,
    },
    { name: t('boards:create.defaultColumn.done'), iql: 'statusCategory = done', wip_limit: null },
  ]
}

export function BoardsScreen() {
  const { t } = useTranslation(['boards', 'common', 'issues'])
  const queryClient = useQueryClient()
  const projects = useProjects()
  const [projectId, setProjectId] = useState<string | null>(null)
  const [name, setName] = useState('')

  const boards = useQuery({
    queryKey: ['boards', 'list', projectId],
    queryFn: () => boardsApi.list(projectId as string),
    enabled: projectId !== null,
  })

  const create = useMutation({
    mutationFn: () =>
      boardsApi.create({
        project_id: projectId as string,
        name,
        columns: defaultColumns(t),
      }),
    onSuccess: () => {
      setName('')
      void queryClient.invalidateQueries({ queryKey: ['boards', 'list', projectId] })
    },
  })

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <h1 className="text-xl font-semibold">{t('boards:list.title')}</h1>

      <Select
        label={t('issues:list.project')}
        value={projectId ?? ''}
        onChange={(e) => { setProjectId(e.target.value || null); }}
      >
        <option value="">{t('issues:list.projectAll')}</option>
        {projects.data?.items.map((p) => (
          <option key={p.id} value={p.id}>{p.key} · {p.name}</option>
        ))}
      </Select>

      {projectId === null ? null : boards.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : boards.isError ? (
        <Alert>{describeError(boards.error)}</Alert>
      ) : boards.data.length === 0 ? (
        <Card className="text-center">
          <p className="font-medium">{t('boards:list.empty')}</p>
          <p className="mt-1 text-sm text-muted">{t('boards:list.emptyHint')}</p>
        </Card>
      ) : (
        <ul className="flex flex-col gap-2">
          {boards.data.map((board) => (
            <li key={board.id}>
              <Card className="flex items-center gap-3 py-3">
                <span className="font-medium">{board.name}</span>
                <span className="text-xs text-muted">
                  {board.columns.map((c) => c.name).join(' · ')}
                </span>
                <Link
                  to="/boards/$boardId"
                  params={{ boardId: board.id }}
                  className="ml-auto text-sm text-accent hover:underline"
                >
                  {t('boards:list.open')}
                </Link>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {projectId !== null ? (
        <Card>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => { event.preventDefault(); create.mutate() }}
          >
            <h2 className="text-sm font-medium text-muted">{t('boards:create.title')}</h2>
            {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
            <Field
              label={t('boards:create.name')}
              required
              value={name}
              onChange={(e) => { setName(e.target.value); }}
            />
            <p className="text-xs text-muted">{t('boards:create.useDefaults')}</p>
            <Button type="submit" loading={create.isPending} disabled={name.trim() === ''}>
              {t('boards:create.submit')}
            </Button>
          </form>
        </Card>
      ) : null}
    </section>
  )
}
