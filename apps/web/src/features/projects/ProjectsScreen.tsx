import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

export function ProjectsScreen() {
  const { t } = useTranslation(['projects', 'common'])
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [search, setSearch] = useState('')

  /**
   * 커서 페이지네이션. 한 페이지만 받고 말면 프로젝트가 페이지 크기를 넘는
   * 순간 나머지가 화면에서 **사라진다** — 있는데 갈 수가 없다.
   */
  const projects = useInfiniteQuery({
    queryKey: ['projects', 'list', search],
    queryFn: ({ pageParam }) =>
      projectsApi.list({
        ...(pageParam ? { cursor: pageParam } : {}),
        ...(search.trim() ? { q: search.trim() } : {}),
      }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // 검색어를 고칠 때마다 목록이 사라졌다 나타나면 눈이 아프다.
    placeholderData: (previous) => previous,
  })

  if (projects.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (projects.isError) {
    return <Alert>{describeError(projects.error)}</Alert>
  }

  const items = projects.data.pages.flatMap((page) => page.items)

  return (
    <section className="mx-auto max-w-3xl">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t('projects:list.title')}</h1>
        <Button onClick={() => { setCreating((v) => !v); }}>{t('projects:create.title')}</Button>
      </header>

      {/* 페이지를 넘겨 가며 찾게 하지 않는다. 프로젝트가 수십 개만 돼도
          "더 보기" 를 몇 번씩 누르는 건 검색이 아니다. */}
      <Field
        label={t('projects:list.search')}
        className="mt-4"
        value={search}
        onChange={(e) => { setSearch(e.target.value); }}
      />

      {creating ? (
        <CreateProjectForm
          onCreated={() => {
            setCreating(false)
            void (async () => {
              // 먼저 취소한다. 만드는 동안 검색어를 친 경우, 그 목록 질의가
              // 생성보다 늦게 끝나면 "없음" 이라는 낡은 답이 무효화 뒤에
              // 앉아 버려서 방금 만든 프로젝트가 안 보인다.
              await queryClient.cancelQueries({ queryKey: ['projects', 'list'] })
              await queryClient.invalidateQueries({ queryKey: ['projects', 'list'] })
            })()
          }}
        />
      ) : null}

      {items.length === 0 ? (
        <Card className="mt-6 text-center">
          {/* 검색 결과가 없는 것과 프로젝트가 없는 것은 다른 상황이다.
              "첫 프로젝트를 만드세요" 라고 하면 있는 걸 없다고 말하는 셈이다. */}
          <p className="font-medium">
            {search.trim() ? t('projects:list.noMatch') : t('projects:list.empty')}
          </p>
          {search.trim() ? null : (
            <p className="mt-1 text-sm text-muted">{t('projects:list.emptyHint')}</p>
          )}
        </Card>
      ) : (
        <ul className="mt-6 flex flex-col gap-2">
          {items.map((project) => (
            <li key={project.id}>
              <Card className="flex items-baseline gap-3 py-3">
                <code className="rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-muted">
                  {project.key}
                </code>
                <span className="font-medium">{project.name}</span>
                {project.archived_at ? (
                  <span className="ml-auto text-xs text-muted">
                    {t('projects:detail.archived')}
                  </span>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      )}

      {projects.hasNextPage ? (
        <Button
          variant="secondary"
          className="mt-3 self-start"
          loading={projects.isFetchingNextPage}
          onClick={() => { void projects.fetchNextPage() }}
        >
          {t('common:action.loadMore')}
        </Button>
      ) : null}
    </section>
  )
}

function CreateProjectForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useTranslation(['projects', 'common'])
  const [key, setKey] = useState('')
  const [name, setName] = useState('')

  const create = useMutation({
    mutationFn: () => projectsApi.create({ key: key.toUpperCase(), name }),
    onSuccess: onCreated,
  })

  return (
    <Card className="mt-4">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
        <Field
          label={t('projects:create.key')}
          hint={t('projects:create.keyHint', { example: 'ENG-123' })}
          name="key"
          required
          value={key}
          // 서버가 대문자만 받으므로 입력 단계에서 맞춰준다.
          onChange={(e) => { setKey(e.target.value.toUpperCase()); }}
        />
        <Field
          label={t('projects:create.name')}
          name="name"
          required
          value={name}
          onChange={(e) => { setName(e.target.value); }}
        />
        <Button type="submit" loading={create.isPending}>
          {t('projects:create.submit')}
        </Button>
      </form>
    </Card>
  )
}
