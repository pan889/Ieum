import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

export function ProjectsScreen() {
  const { t } = useTranslation(['projects', 'common'])
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)

  const projects = useQuery({
    queryKey: ['projects', 'list'],
    queryFn: () => projectsApi.list(),
  })

  if (projects.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (projects.isError) {
    return <Alert>{describeError(projects.error)}</Alert>
  }

  return (
    <section className="mx-auto max-w-3xl">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t('projects:list.title')}</h1>
        <Button onClick={() => { setCreating((v) => !v); }}>{t('projects:create.title')}</Button>
      </header>

      {creating ? (
        <CreateProjectForm
          onCreated={() => {
            setCreating(false)
            void queryClient.invalidateQueries({ queryKey: ['projects', 'list'] })
          }}
        />
      ) : null}

      {projects.data.items.length === 0 ? (
        <Card className="mt-6 text-center">
          <p className="font-medium">{t('projects:list.empty')}</p>
          <p className="mt-1 text-sm text-muted">{t('projects:list.emptyHint')}</p>
        </Card>
      ) : (
        <ul className="mt-6 flex flex-col gap-2">
          {projects.data.items.map((project) => (
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
