import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

import { useSpaces } from './hooks'

export function SpacesScreen() {
  const { t } = useTranslation(['wiki', 'common'])
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)

  const spaces = useSpaces(search)

  if (spaces.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (spaces.isError) {
    return <Alert>{describeError(spaces.error)}</Alert>
  }

  const items = spaces.data.pages.flatMap((page) => page.items)

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t('wiki:spaces.title')}</h1>
        <Button onClick={() => { setCreating((v) => !v) }}>{t('wiki:spaces.create')}</Button>
      </header>

      <Field
        label={t('wiki:spaces.search')}
        value={search}
        onChange={(e) => { setSearch(e.target.value) }}
      />

      {creating ? <CreateSpaceForm onCreated={() => { setCreating(false) }} /> : null}

      {items.length === 0 ? (
        <Card className="text-center">
          <p className="font-medium">
            {search.trim() ? t('wiki:spaces.noMatch') : t('wiki:spaces.empty')}
          </p>
          {search.trim() ? null : (
            <p className="mt-1 text-sm text-muted">{t('wiki:spaces.emptyHint')}</p>
          )}
        </Card>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((space) => (
            <li key={space.id}>
              <Card className="flex items-baseline gap-3 py-3">
                <code className="rounded bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-muted">
                  {space.key}
                </code>
                <span className="font-medium">{space.name}</span>
                {space.description ? (
                  <span className="truncate text-xs text-muted">{space.description}</span>
                ) : null}
                <Link
                  to="/wiki/$spaceKey"
                  params={{ spaceKey: space.key }}
                  className="ml-auto shrink-0 text-sm text-accent hover:underline"
                >
                  {t('wiki:spaces.open')}
                </Link>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {spaces.hasNextPage ? (
        <Button
          variant="secondary"
          className="self-start"
          loading={spaces.isFetchingNextPage}
          onClick={() => { void spaces.fetchNextPage() }}
        >
          {t('common:action.loadMore')}
        </Button>
      ) : null}
    </section>
  )
}

function CreateSpaceForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'team' | 'personal' | 'kb'>('team')

  const create = useMutation({
    mutationFn: () => wikiApi.spaces.create({ key: key.toUpperCase(), name, kind }),
    onSuccess: () => {
      onCreated()
      void queryClient.invalidateQueries({ queryKey: ['wiki', 'spaces'] })
    },
  })

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => { event.preventDefault(); create.mutate() }}
      >
        {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
        <Field
          label={t('wiki:spaces.key')}
          hint={t('wiki:spaces.keyHint')}
          required
          value={key}
          // 서버가 대문자만 받으므로 입력 단계에서 맞춰준다.
          onChange={(e) => { setKey(e.target.value.toUpperCase()) }}
        />
        <Field
          label={t('wiki:spaces.name')}
          required
          value={name}
          onChange={(e) => { setName(e.target.value) }}
        />
        <Select
          label={t('wiki:spaces.kind')}
          value={kind}
          onChange={(e) => { setKind(e.target.value as 'team' | 'personal' | 'kb') }}
        >
          <option value="team">{t('wiki:spaces.kindTeam')}</option>
          <option value="personal">{t('wiki:spaces.kindPersonal')}</option>
          <option value="kb">{t('wiki:spaces.kindKb')}</option>
        </Select>
        <Button type="submit" loading={create.isPending}>
          {t('wiki:spaces.submit')}
        </Button>
      </form>
    </Card>
  )
}
