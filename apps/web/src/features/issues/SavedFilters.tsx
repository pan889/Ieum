/**
 * 저장 필터 줄.
 *
 * 필터를 불러오면 그 IQL 이 URL 로 간다 — 필터 id 가 아니라. 링크를 받은
 * 사람이 남의 비공개 필터를 열 수는 없지만 질의 자체는 실행할 수 있어야
 * 하고(각자 자기 권한으로 돈다), URL 만 봐도 무엇을 보는지 알 수 있다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { searchApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Checkbox, Field, Chip } from '@/shared/ui/primitives'

export interface SavedFiltersProps {
  /** 지금 화면이 보여 주는 질의. "현재 질의 저장" 이 이걸 쓴다. */
  activeIql: string
  onLoad: (iql: string) => void
}

export function SavedFilters({ activeIql, onLoad }: SavedFiltersProps) {
  const { t } = useTranslation(['issues', 'common'])
  const queryClient = useQueryClient()
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const [shared, setShared] = useState(false)
  const [copied, setCopied] = useState(false)

  const filters = useQuery({
    queryKey: ['filters', 'list'],
    queryFn: () => searchApi.filters.list(),
    staleTime: 60_000,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['filters', 'list'] })

  const save = useMutation({
    mutationFn: () =>
      searchApi.filters.create({ name: name.trim(), iql: activeIql, is_shared: shared }),
    onSuccess: () => {
      setNaming(false)
      setName('')
      setShared(false)
      void invalidate()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => searchApi.filters.remove(id),
    onSuccess: () => invalidate(),
  })

  const rows = filters.data ?? []
  const canSave = activeIql.trim() !== '' && name.trim() !== ''

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="w-[4.5rem] shrink-0 text-2xs font-semibold uppercase tracking-wide text-subtle">
          {t('issues:filters.saved')}
        </span>

        {rows.length === 0 ? (
          <span className="text-xs text-subtle">{t('issues:filters.none')}</span>
        ) : (
          rows.map((filter) => (
            <span key={filter.id} className="inline-flex items-center">
              <Chip
                pressed={filter.iql === activeIql}
                onClick={() => { onLoad(filter.iql) }}
                title={filter.iql}
              >
                {filter.name}
                {filter.is_shared ? ' ·' : ''}
              </Chip>
              <button
                type="button"
                className="ml-0.5 px-1 text-xs text-muted hover:text-danger"
                aria-label={t('issues:filters.delete', { name: filter.name })}
                onClick={() => { remove.mutate(filter.id) }}
              >
                ×
              </button>
            </span>
          ))
        )}

        {/* 지금 URL 이 곧 이 목록이다. 주소창을 긁게 하지 않는다. */}
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => {
            void copyLink().then((ok) => {
              if (ok) {
                setCopied(true)
                setTimeout(() => { setCopied(false) }, 2000)
              }
            })
          }}
        >
          {copied ? t('issues:filters.linkCopied') : t('issues:filters.copyLink')}
        </Button>

        <Button
          variant="ghost"
          size="sm"
          // 빈 질의를 저장하면 "전체 이슈" 라는 이름뿐인 필터가 생긴다.
          disabled={activeIql.trim() === ''}
          title={activeIql.trim() === '' ? t('issues:filters.needsQuery') : undefined}
          onClick={() => { setNaming((v) => !v) }}
        >
          {t('issues:filters.save')}
        </Button>
      </div>

      {naming ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => { event.preventDefault(); save.mutate() }}
        >
          <Field
            label={t('issues:filters.name')}
            className="text-sm"
            value={name}
            onChange={(e) => { setName(e.target.value) }}
          />
          {/* 이 줄은 `items-end` 다. 프리미티브의 감싸개에는 여백이 없으므로
              옆의 입력·버튼과 아랫변을 맞추는 여백은 여기서 준다. */}
          <div className="py-2">
            <Checkbox
              className="size-4 rounded border-border accent-accent"
              label={t('issues:filters.share')}
              checked={shared}
              onChange={(e) => { setShared(e.target.checked) }}
            />
          </div>
          <Button type="submit" loading={save.isPending} disabled={!canSave}>
            {t('common:action.save')}
          </Button>
          <Button variant="ghost" onClick={() => { setNaming(false) }}>
            {t('common:action.cancel')}
          </Button>
        </form>
      ) : null}

      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
    </div>
  )
}

/**
 * 현재 URL 을 클립보드로.
 *
 * `navigator.clipboard` 는 보안 컨텍스트(https 또는 localhost)에서만 있다.
 * 없으면 조용히 실패하고 "복사됨" 을 띄우지 않는다 — 안 됐는데 됐다고
 * 하는 쪽이 나쁘다.
 */
async function copyLink(): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(window.location.href)
    return true
  } catch {
    return false
  }
}
