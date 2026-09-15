import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Project } from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { boardsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, EmptyState, Field, PageHeader } from '@/shared/ui/primitives'

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
  // 프로젝트는 검색으로 고른다. 전체를 `<select>` 에 넣으면 설치가 커진
  // 순간 뒤쪽 프로젝트의 보드를 볼 길이 사라진다(ux-principles 4절).
  const [project, setProject] = useState<Project | null>(null)
  const projectId = project?.id ?? null
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
      <PageHeader title={t('boards:list.title')} />

      <ProjectPicker
        label={t('issues:list.project')}
        chosen={project}
        onPick={setProject}
      />

      {/* **빈 화면을 그냥 비워 두지 않는다.** 여기는 `null` 이었고, 그래서
          프로젝트를 고르기 전의 보드 화면은 제목 한 줄과 고르는 줄만 떠 있는
          백지였다 — 처음 온 사람은 고장인지 아직 안 고른 것인지 모른다. */}
      {projectId === null ? (
        <EmptyState
          title={t('common:state.pickProject')}
          description={t('common:state.pickProjectHint')}
        />
      ) : boards.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : boards.isError ? (
        <Alert>{describeError(boards.error)}</Alert>
      ) : boards.data.length === 0 ? (
        <EmptyState
          title={t('boards:list.empty')}
          description={t('boards:list.emptyHint')}
        />
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
            <h2 className="text-sm font-semibold text-fg">{t('boards:create.title')}</h2>
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
