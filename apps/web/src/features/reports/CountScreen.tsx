import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CountReport, ReportBucket } from '@ieum/api-client'

import { useUserNames } from '@/features/issues/hooks'
import { reportsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { CountBars } from '@/shared/ui/CountBars'
import { Alert, Button, Card, Field, PageHeader, Select } from '@/shared/ui/primitives'

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
      <PageHeader title={t('reports:title')} />

      <Card>
        {/*
          질의 한 줄과 고르개 하나에 폭 768을 다 쓰고, **꽉 찬 파란 막대**가
          바닥에 깔려 있었다. 전송 단추는 자기 글자만큼만 넓다 — 폼 폭을 꽉
          채운 단추는 손가락으로 누르는 화면의 모양이지 업무 도구가 아니다.
        */}
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            run.mutate({ iql, group_by: groupBy })
          }}
        >
          <div className="min-w-64 flex-1">
            <Field
              label={t('reports:query')}
              hint={t('reports:queryHint')}
              className="w-full"
              value={iql}
              onChange={(e) => { setIql(e.target.value) }}
            />
          </div>
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
          {/* 힌트 한 줄이 입력 아래에 붙으므로 `items-end` 만으로는 밑변이
              안 맞는다. 입력 칸과 같은 높이로 세운다. */}
          <Button type="submit" className="mb-px" loading={run.isPending}>
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
  const label = (bucket: ReportBucket): string => {
    if (bucket.key === null) return t('reports:none')
    if (report.group_by === 'assignee' || report.group_by === 'reporter') {
      return names.data?.get(bucket.key) ?? bucket.key
    }
    return bucket.key
  }

  return (
    <Card className="flex flex-col gap-3">
      {/*
        그리는 규칙(이름·수를 글자로, 합이 안 맞는 이유를 적는다)은 문서 안의
        `::chart` 와 **같은 컴포넌트**가 갖고 있다. 각자 그리면 규칙 하나가
        한쪽에서만 지켜지는 날이 온다.
      */}
      <CountBars report={report} label={label} />

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
