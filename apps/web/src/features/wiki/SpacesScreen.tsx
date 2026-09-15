import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, EmptyState, Field, PageHeader, Select } from '@/shared/ui/primitives'

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
      <PageHeader
        title={t('wiki:spaces.title')}
        actions={
          <>
            {/* 내 할 일로 가는 길. 위키의 첫 화면에 둔다 — 태스크는 문서에서
                오므로 문서를 여는 자리에서 이어지는 것이 자연스럽다.

                전에는 `ml-auto` 로 밀어 놓은 맨 글자였고, 옆의 단추와 겹쳐
                찍혔다("My tasks" 위에 "New space" 가 올라탔다). 머리의
                조작 자리는 하나이고 그 안에서 나란히 선다. */}
            <Link
              to="/wiki/tasks"
              className="text-sm font-medium text-accent hover:underline"
            >
              {t('wiki:tasks.mineTitle')}
            </Link>
            <Button onClick={() => { setCreating((v) => !v) }}>
              {t('wiki:spaces.create')}
            </Button>
          </>
        }
      />

      <Field
        label={t('wiki:spaces.search')}
        labelHidden
        placeholder={t('wiki:spaces.search')}
        className="max-w-sm"
        value={search}
        onChange={(e) => { setSearch(e.target.value) }}
      />

      {creating ? <CreateSpaceForm onCreated={() => { setCreating(false) }} /> : null}

      {items.length === 0 ? (
        <EmptyState
          title={search.trim() ? t('wiki:spaces.noMatch') : t('wiki:spaces.empty')}
          description={search.trim() ? undefined : t('wiki:spaces.emptyHint')}
        />
      ) : (
        /* 한 줄짜리 정보에 카드를 하나씩 주지 않는다 — 목록은 한 상자 안에서
           줄로 나뉜다(`ProjectsScreen` 과 같은 이유). */
        <ul className="divide-y divide-border overflow-hidden rounded-card border border-border bg-surface shadow-raised">
          {items.map((space) => (
            <li
              key={space.id}
              className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-surface-raised"
            >
              <code className="shrink-0 rounded border border-border bg-sunken px-1.5 py-0.5 font-mono text-xs text-muted">
                {space.key}
              </code>
              <span className="shrink-0 font-medium text-fg">{space.name}</span>
              {space.description ? (
                <span className="min-w-0 flex-1 truncate text-xs text-subtle">
                  {space.description}
                </span>
              ) : (
                <span className="flex-1" />
              )}
              <Link
                to="/wiki/$spaceKey"
                params={{ spaceKey: space.key }}
                className="shrink-0 text-sm font-medium text-accent hover:underline"
              >
                {t('wiki:spaces.open')}
              </Link>
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
      void (async () => {
        // 먼저 취소한다. 만드는 동안 검색어를 친 경우, 그 목록 질의가
        // 생성보다 늦게 끝나면 "없음" 이라는 낡은 답이 무효화 뒤에 앉아
        // 버려서 방금 만든 스페이스가 안 보인다.
        await queryClient.cancelQueries({ queryKey: ['wiki', 'spaces'] })
        await queryClient.invalidateQueries({ queryKey: ['wiki', 'spaces'] })
      })()
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
