/**
 * 감사 로그 조회 (auth.md 6절).
 *
 * 쓰기만 되고 읽을 길이 없으면 감사에 대응할 수 없다. 여기가 그 길이다.
 *
 * 기간을 기본으로 좁혀서 연다. 안 좁히면 첫 화면이 테이블 전체를 훑고,
 * 감사 담당자가 찾는 것은 거의 언제나 "최근" 이다.
 */

import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AuditEntry, AuditQuery } from '@ieum/api-client'

import { formatDateTime } from '@/features/issues/format'
import { auditApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { saveBlob } from '@/shared/download'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

const PAGE_SIZE = 50

/** 기본 조회 기간. 오늘부터 거슬러 올라간다. */
const DEFAULT_DAYS = 7

function daysAgo(days: number): string {
  const when = new Date()
  when.setDate(when.getDate() - days)
  // `<input type="date">` 는 `YYYY-MM-DD` 만 받는다.
  return when.toISOString().slice(0, 10)
}

/**
 * 날짜 칸의 값을 서버가 읽는 시각으로.
 *
 * 끝 날짜는 **다음 날 0시**로 보낸다. 그 날 0시로 보내면 마지막 날이 통째로
 * 빠지고, 사용자는 "오늘 것이 안 나온다" 고 본다.
 */
function boundary(date: string, { inclusive }: { inclusive: boolean }): string | undefined {
  if (!date) return undefined
  const when = new Date(`${date}T00:00:00`)
  if (!inclusive) when.setDate(when.getDate() + 1)
  return when.toISOString()
}

export function AuditLogScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const [action, setAction] = useState('')
  const [since, setSince] = useState(daysAgo(DEFAULT_DAYS))
  const [until, setUntil] = useState('')

  const from = boundary(since, { inclusive: true })
  const to = boundary(until, { inclusive: false })
  const filters: AuditQuery = {
    ...(action ? { action } : {}),
    ...(from ? { since: from } : {}),
    ...(to ? { until: to } : {}),
  }

  const actions = useQuery({
    queryKey: ['audit', 'actions'],
    queryFn: () => auditApi.actions(),
    staleTime: 300_000,
  })

  const entries = useInfiniteQuery({
    queryKey: ['audit', filters],
    queryFn: ({ pageParam }) =>
      auditApi.list({
        ...filters,
        limit: PAGE_SIZE,
        ...(pageParam ? { cursor: pageParam } : {}),
      }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })

  const download = useMutation({
    mutationFn: async () => {
      const stamp = new Date().toISOString().slice(0, 10)
      saveBlob(await auditApi.exportCsv(filters), `ieum-audit-${stamp}.csv`)
    },
  })

  const rows = entries.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-5">
      <SettingsNav />
      <header>
        <h1 className="text-xl font-semibold">{t('admin:audit.title')}</h1>
        <p className="mt-1 text-sm text-muted">{t('admin:audit.description')}</p>
      </header>

      <Card className="flex flex-wrap items-end gap-3">
        <Select
          label={t('admin:audit.action')}
          value={action}
          onChange={(event) => { setAction(event.target.value) }}
        >
          <option value="">{t('admin:audit.allActions')}</option>
          {/* 점으로 끝나면 그 영역 전체다. 하나하나 고르지 않아도 된다. */}
          <option value="auth.">{t('admin:audit.areaAuth')}</option>
          <option value="identity.">{t('admin:audit.areaIdentity')}</option>
          {(actions.data ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
        <Field
          label={t('admin:audit.since')}
          type="date"
          value={since}
          onChange={(event) => { setSince(event.target.value) }}
        />
        <Field
          label={t('admin:audit.until')}
          type="date"
          value={until}
          onChange={(event) => { setUntil(event.target.value) }}
        />
        <Button
          variant="secondary"
          loading={download.isPending}
          onClick={() => { download.mutate() }}
        >
          {t('admin:audit.export')}
        </Button>
      </Card>

      {entries.isError ? <Alert>{describeError(entries.error)}</Alert> : null}
      {download.isError ? <Alert>{describeError(download.error)}</Alert> : null}

      {rows.length === 0 && !entries.isPending ? (
        <p className="text-sm text-muted">{t('admin:audit.empty')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[48rem] text-left text-sm">
            <thead className="border-b border-border text-xs uppercase text-muted">
              <tr>
                <th className="py-2 pr-3 font-medium">{t('admin:audit.when')}</th>
                <th className="py-2 pr-3 font-medium">{t('admin:audit.action')}</th>
                <th className="py-2 pr-3 font-medium">{t('admin:audit.actor')}</th>
                <th className="py-2 pr-3 font-medium">{t('admin:audit.target')}</th>
                <th className="py-2 pr-3 font-medium">{t('admin:audit.ip')}</th>
                <th className="py-2 font-medium">{t('admin:audit.details')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <AuditRow key={row.id} entry={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {entries.hasNextPage ? (
        <Button
          variant="secondary"
          className="self-start text-xs"
          loading={entries.isFetchingNextPage}
          onClick={() => { void entries.fetchNextPage() }}
        >
          {t('common:pagination.loadMore')}
        </Button>
      ) : null}
    </section>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const { t } = useTranslation('admin')
  const details = Object.entries(entry.metadata)

  return (
    <tr className="border-b border-border/60 align-top">
      <td className="whitespace-nowrap py-2 pr-3 text-muted">{formatDateTime(entry.created_at)}</td>
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-xs">{entry.action}</td>
      {/* 지워진 계정도 로그에는 남는다. 빈 칸으로 두면 누구였는지 알 수 없다. */}
      <td className="py-2 pr-3">{entry.actor_email ?? t('audit.systemActor')}</td>
      {/* 종류만 보여 주면 "user" 만 늘어서서 아무 줄도 구분되지 않는다.
          id 앞자리를 붙여 다른 기록·다른 시스템과 맞춰 볼 수 있게 한다. */}
      <td className="whitespace-nowrap py-2 pr-3 text-muted">
        {entry.target_type ?? ''}
        {entry.target_id ? (
          <span className="ml-1 font-mono text-xs">{entry.target_id.slice(0, 8)}</span>
        ) : null}
      </td>
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-xs text-muted">{entry.ip ?? ''}</td>
      <td className="py-2 text-xs text-muted">
        {details.map(([key, value]) => (
          <span key={key} className="mr-2 whitespace-nowrap">
            {key}=<span className="font-mono">{String(value)}</span>
          </span>
        ))}
      </td>
    </tr>
  )
}
