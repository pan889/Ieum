/**
 * 이 티켓이 무엇에 대한 것인가 — 붙은 자산 (C15, M6).
 *
 * **고르는 것은 검색이다.** 자산은 수천 개가 되고, 목록을 드롭다운에 넣으면
 * 마지막에 등록한 장비를 고를 수 없다 — 이 저장소가 세 번 겪은 일이다
 * (ux-principles 4절). 한 글자도 안 쳤을 때 후보를 안 보여 주는 것도 일부러다:
 * 첫 스무 개를 보여 주면 "이게 전부인가" 로 읽힌다.
 *
 * **나간 장비는 후보에 없다.** 서버가 기본으로 뺀다 — 고를 때 섞이면 잘못
 * 고른다. 이미 이어져 있던 자산이 나중에 사용 종료돼도 목록에는 그대로 남고,
 * 상태를 함께 보여 준다.
 *
 * 붙은 것이 없으면 칸을 내지 않는다. 대부분의 이슈는 티켓조차 아니다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AssetStatus } from '@ieum/api-client'

import { assetsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

/** 후보 수. 더 좁히는 것은 사람이 더 치는 쪽이 빠르다. */
const LIMIT = 10

const TONE: Record<AssetStatus, 'done' | 'neutral' | 'danger' | 'todo'> = {
  in_use: 'done',
  spare: 'neutral',
  repair: 'danger',
  retired: 'neutral',
}

export function Assets({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const trimmed = query.trim()

  const linked = useQuery({
    queryKey: ['assets', 'linked', issueId],
    queryFn: () => assetsApi.linked(issueId),
    staleTime: 30_000,
  })

  const candidates = useQuery({
    queryKey: ['assets', 'search', trimmed],
    queryFn: () => assetsApi.search({ q: trimmed, limit: LIMIT }),
    // 한 글자도 안 쳤으면 묻지 않는다 (파일 머리 참조).
    enabled: adding && trimmed.length > 0,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['assets'] })

  const link = useMutation({
    mutationFn: (assetId: string) => assetsApi.link(issueId, assetId),
    onSuccess: async () => {
      setQuery('')
      setAdding(false)
      await refresh()
    },
  })

  const unlink = useMutation({
    mutationFn: (assetId: string) => assetsApi.unlink(issueId, assetId),
    onSuccess: refresh,
  })

  const rows = linked.data ?? []
  // 붙은 것도 없고 손잡이도 안 열었으면 자리를 차지하지 않는다. 단,
  // 오류는 숨기지 않는다 — 못 읽은 것과 없는 것은 다르다.
  if (!linked.isError && rows.length === 0 && !adding) {
    return (
      <div>
        <Button
          variant="ghost"
          className="text-xs"
          onClick={() => { setAdding(true) }}
        >
          {t('desk:asset.linkAdd')}
        </Button>
      </div>
    )
  }

  return (
    <Card className="flex flex-col gap-3" data-testid="issue-assets">
      <h2 className="text-sm font-medium text-muted">{t('desk:asset.linkTitle')}</h2>
      {linked.isError ? <Alert>{describeError(linked.error)}</Alert> : null}
      {link.isError ? <Alert>{describeError(link.error)}</Alert> : null}
      {unlink.isError ? <Alert>{describeError(unlink.error)}</Alert> : null}

      <ul className="flex flex-col gap-1.5 text-sm">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-baseline gap-2">
            <span className="text-xs text-muted">{row.type_name}</span>
            <span className="min-w-0 flex-1 truncate">{row.name}</span>
            {row.tag ? <code className="font-mono text-xs">{row.tag}</code> : null}
            <Badge tone={TONE[row.status]}>{t(`desk:asset.status.${row.status}`)}</Badge>
            {row.location ? <span className="text-xs text-muted">{row.location}</span> : null}
            {row.organization_name ? (
              <span className="text-xs text-muted">{row.organization_name}</span>
            ) : null}
            <Button
              variant="ghost"
              className="text-xs"
              loading={unlink.isPending && unlink.variables === row.id}
              onClick={() => { unlink.mutate(row.id) }}
            >
              {t('desk:asset.unlink')}
            </Button>
          </li>
        ))}
      </ul>

      {adding ? (
        <div className="flex flex-col gap-2">
          <Field
            label={t('desk:asset.find')}
            hint={t('desk:asset.findHint')}
            value={query}
            onChange={(event) => { setQuery(event.target.value) }}
          />
          {candidates.isError ? <Alert>{describeError(candidates.error)}</Alert> : null}
          <ul
            className="flex flex-col items-start gap-1"
            aria-label={t('common:state.matches')}
          >
            {(candidates.data?.items ?? []).map((row) => (
              <li key={row.id}>
                <Button
                  variant="ghost"
                  className="text-xs"
                  loading={link.isPending && link.variables === row.id}
                  onClick={() => { link.mutate(row.id) }}
                >
                  {row.name}
                  {row.tag ? ` · ${row.tag}` : ''}
                  {row.location ? ` · ${row.location}` : ''}
                </Button>
              </li>
            ))}
          </ul>
          {/* 잘렸으면 **잘렸다고 말한다.** 조용히 자르면 사람은 "이게 전부"
              라고 읽는다. */}
          {(candidates.data?.next_cursor ?? null) !== null ? (
            <p className="text-xs text-muted">{t('desk:asset.findMore')}</p>
          ) : null}
          {candidates.isSuccess && candidates.data.items.length === 0 ? (
            <p className="text-xs text-muted">{t('desk:asset.findEmpty')}</p>
          ) : null}
          <Button variant="ghost" className="self-start text-xs" onClick={() => { setAdding(false) }}>
            {t('common:action.close')}
          </Button>
        </div>
      ) : (
        <Button variant="ghost" className="self-start text-xs" onClick={() => { setAdding(true) }}>
          {t('desk:asset.linkAdd')}
        </Button>
      )}
    </Card>
  )
}
