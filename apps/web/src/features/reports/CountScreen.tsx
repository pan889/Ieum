import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CountReport, ReportBucket } from '@ieum/api-client'

import { useUserNames } from '@/features/issues/hooks'
import { reportsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

/** 기준 목록은 **서버가 준다**. 손으로 들면 새 기준이 늘 때 화면만 모른다. */
const FALLBACK_GROUPS = ['status', 'assignee', 'priority', 'type'] as const

export function CountScreen() {
  const { t } = useTranslation(['reports', 'common'])
  const [iql, setIql] = useState('')
  const [groupBy, setGroupBy] = useState('status')

  const run = useMutation({
    mutationFn: (body: { iql: string; group_by: string }) => reportsApi.count(body),
  })
  const report = run.data

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <h1 className="text-xl font-semibold">{t('reports:title')}</h1>

      <Card>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            run.mutate({ iql, group_by: groupBy })
          }}
        >
          <Field
            label={t('reports:query')}
            hint={t('reports:queryHint')}
            value={iql}
            onChange={(e) => { setIql(e.target.value) }}
          />
          <Select
            label={t('reports:groupBy')}
            value={groupBy}
            onChange={(e) => { setGroupBy(e.target.value) }}
          >
            {(report?.supported ?? FALLBACK_GROUPS).map((name) => (
              <option key={name} value={name}>
                {t(`reports:group.${name}`)}
              </option>
            ))}
          </Select>
          <Button type="submit" loading={run.isPending}>
            {t('reports:run')}
          </Button>
        </form>
      </Card>

      {run.isError ? <Alert>{describeError(run.error)}</Alert> : null}
      {report === undefined ? null : <Result report={report} iql={iql} />}
    </section>
  )
}

function Result({ report, iql }: { report: CountReport; iql: string }) {
  const { t } = useTranslation(['reports'])
  // 담당자·보고자 칸의 키는 UUID 다. 이름을 붙여 줘야 읽을 수 있다.
  const names = useUserNames(
    report.group_by === 'assignee' || report.group_by === 'reporter'
      ? report.buckets.map((b) => b.key)
      : [],
  )
  const biggest = Math.max(1, ...report.buckets.map((b) => b.count))

  const label = (bucket: ReportBucket): string => {
    if (bucket.key === null) return t('reports:none')
    if (report.group_by === 'assignee' || report.group_by === 'reporter') {
      return names.data?.get(bucket.key) ?? bucket.key
    }
    return bucket.key
  }

  return (
    <Card className="flex flex-col gap-3">
      <p className="text-sm font-medium">{t('reports:total', { count: report.total })}</p>

      {/*
        **합이 총계를 넘는 이유를 적는다.** 안 적으면 사람은 숫자가 안 맞는
        것을 버그로 보고, 그다음부터 리포트를 안 믿는다.
      */}
      {report.multi_valued ? (
        <p className="text-xs text-muted">{t('reports:multiValued')}</p>
      ) : null}
      {report.truncated ? <Alert>{t('reports:truncated')}</Alert> : null}

      {report.buckets.length === 0 ? (
        <p className="text-sm text-muted">{t('reports:empty')}</p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {report.buckets.map((bucket) => (
              <tr key={bucket.key ?? '∅'}>
                <th scope="row" className="w-40 py-1 pr-3 text-left font-normal">
                  {label(bucket)}
                </th>
                <td className="py-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-3 rounded bg-accent/40"
                      style={{ width: `${String((bucket.count / biggest) * 100)}%` }}
                      aria-hidden
                    />
                    <span className="tabular-nums text-xs text-muted">{bucket.count}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/*
        **숫자에서 목록으로 갈 수 있어야 한다.** "40건" 을 보고 "어떤 40건
        인가" 를 물을 수 없으면 리포트는 막다른 길이다.
      */}
      <Link
        to="/issues"
        search={iql === '' ? {} : { iql }}
        className="text-sm text-accent hover:underline"
      >
        {t('reports:share')}
      </Link>
    </Card>
  )
}
