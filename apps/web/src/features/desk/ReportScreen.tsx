/**
 * 데스크 리포트 — SLA 성적과 상담원별 성적 (feature-map C14).
 *
 * 이 화면의 일은 **숫자를 오해할 수 없게 놓는 것**이다. 서버가 이미 옳게
 * 세므로(`desk/reports.py`), 여기서 틀릴 수 있는 것은 이름과 배치다.
 *
 * 그래서 셋을 나란히 두지 않는다:
 *
 * - **`missed`(늦게 끝남)와 `overdue`(지금 넘김)를 한 칸으로 합치지 않는다.**
 *   앞은 이미 벌어진 일이고 뒤는 지금 손쓸 수 있는 일이다. 합치면 이 화면을
 *   보는 이유가 사라진다.
 * - **평균 소요는 `평균`이라고만 적지 않는다.** SLA 는 업무 달력으로 재고
 *   이 값은 벽시계다 — "평균 2시간" 을 "SLA 4시간" 과 비교하게 두면 그
 *   비교는 언제나 틀린다.
 * - **몇 건 중 몇 건인지 같이 적는다.** 모집단을 안 밝힌 평균은 전체를
 *   뜻하는 것처럼 읽힌다.
 *
 * 합도 화면이 더하지 않는다. `breached`·`total` 을 서버가 주는 이유는 화면이
 * 더하면 서버의 셈과 어긋날 수 있고, 그때 어느 쪽이 진실인지 말할 수 없기
 * 때문이다.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AgentRow, DeskReport, Project, SlaOutcome } from '@ieum/api-client'

import { useUserNames } from '@/features/issues/hooks'
import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

/** 기본 창은 **지난 30일**이다. 열자마자 볼 것이 있어야 한다. */
const DEFAULT_DAYS = 30

export function isoDay(at: Date): string {
  const parts = at.toISOString().split('T')
  return parts[0] ?? ''
}

/**
 * 사람이 고른 두 날짜를 서버가 받는 두 시각으로.
 *
 * **끝나는 날은 그 하루를 포함한다.** 자정으로 보내면 마지막 날에 들어온
 * 티켓이 통째로 빠지는데, 화면에는 그 날짜가 그대로 적혀 있다 — "9월 30일
 * 까지" 를 물어본 사람에게 "9월 29일까지" 를 조용히 답하는 셈이다.
 */
export function windowBounds(from: string, to: string): { starts_at: string; ends_at: string } {
  return { starts_at: `${from}T00:00:00Z`, ends_at: `${to}T23:59:59Z` }
}

/**
 * 초를 사람이 읽는 길이로. 한 시간 미만이면 분으로 말한다.
 *
 * **`0` 을 `—` 로 바꾸지 않는다.** `—` 는 "끝난 것이 없다" 는 뜻이고
 * (`average_wallclock_seconds === null`), 0초는 "정말 즉시 끝났다" 는 뜻이다.
 * 둘을 같은 글자로 쓰면 하나도 못 끝낸 사람과 다 즉시 끝낸 사람이 같아진다.
 */
export function humanize(
  seconds: number,
  t: (key: string, vars: Record<string, number>) => string,
): string {
  if (seconds < 3600) return t('desk:report.minutes', { minutes: Math.round(seconds / 60) })
  return t('desk:report.hours', { hours: Math.round(seconds / 360) / 10 })
}

export function ReportScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const today = new Date()
  const back = new Date(today.getTime() - DEFAULT_DAYS * 86_400_000)
  const [from, setFrom] = useState(isoDay(back))
  const [to, setTo] = useState(isoDay(today))
  const [picked, setPicked] = useState<Project | null>(null)
  /**
   * **물어본 창은 따로 들고 있는다.** 입력 값을 그대로 질의에 쓰면 날짜를
   * 고치는 중간 상태마다 요청이 나가고, 그중 하나가 거절당하면 아직 다 안
   * 고쳤는데 빨간 경고가 뜬다. 처음 값으로 채워 두는 이유는 열자마자 볼
   * 것이 있어야 하기 때문이다.
   */
  const [range, setRange] = useState({ from: isoDay(back), to: isoDay(today) })

  const first = useQuery({
    queryKey: ['projects', 'first'],
    queryFn: () => projectsApi.list({ limit: 1 }),
  })
  const project = picked ?? first.data?.items[0] ?? null
  const projectId = project?.id ?? ''

  const report = useQuery({
    queryKey: ['desk', 'report', projectId, range.from, range.to],
    queryFn: () =>
      deskApi.report({ project_id: projectId, ...windowBounds(range.from, range.to) }),
    enabled: projectId.length > 0 && range.from !== '' && range.to !== '',
  })

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('desk:report.title')}</h1>
        <p className="text-sm text-muted">{t('desk:report.description')}</p>
      </header>

      <ProjectPicker label={t('desk:queues.project')} chosen={project} onPick={setPicked} />

      <Card>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            setRange({ from, to })
          }}
        >
          <Field
            label={t('desk:report.from')}
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value)
            }}
          />
          <Field
            label={t('desk:report.to')}
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value)
            }}
          />
          <Button type="submit" loading={report.isFetching}>
            {t('desk:report.run')}
          </Button>
        </form>
      </Card>

      {report.isError ? <Alert>{describeError(report.error)}</Alert> : null}
      {report.data === undefined ? null : <Result report={report.data} />}
    </section>
  )
}

function Result({ report }: { report: DeskReport }) {
  const { t } = useTranslation(['desk'])

  return (
    <>
      <p className="text-sm font-medium" data-testid="desk-report-tickets">
        {t('desk:report.tickets', { count: report.tickets })}
      </p>
      <SlaTable rows={report.sla} />
      <AgentTable rows={report.agents} />
    </>
  )
}

function SlaTable({ rows }: { rows: SlaOutcome[] }) {
  const { t } = useTranslation(['desk'])

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold">{t('desk:report.slaTitle')}</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:report.slaEmpty')}</p>
      ) : (
        <>
          <table className="w-full text-sm" data-testid="desk-report-sla">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th scope="col" className="py-1 pr-3 font-normal">
                  {t('desk:report.policy')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.met')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.missed')}
                </th>
                {/*
                  **`missed` 와 붙여 놓되 합치지 않는다.** 앞은 이미 끝난
                  이야기고 이 칸은 지금 손쓸 수 있는 것들이다.
                */}
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.overdue')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.running')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.breached')}
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  {t('desk:report.total')}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.policy_id} className="border-t border-line">
                  <th scope="row" className="py-1 pr-3 text-left font-normal">
                    {row.policy_name}
                    <span className="ml-2 text-xs text-muted">
                      {t(`desk:sla.metric.${row.metric}`)}
                    </span>
                  </th>
                  <td className="py-1 pr-3 text-right tabular-nums">{row.met}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">{row.missed}</td>
                  <td className="py-1 pr-3 text-right tabular-nums" data-testid="sla-overdue">
                    {row.overdue}
                  </td>
                  <td className="py-1 pr-3 text-right tabular-nums">{row.running}</td>
                  <td
                    className="py-1 pr-3 text-right font-medium tabular-nums"
                    data-testid="sla-breached"
                  >
                    {row.breached}
                  </td>
                  <td className="py-1 text-right tabular-nums text-muted">{row.total}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {/*
            **위반을 어떻게 셌는지 적는다.** 안 적으면 "알림이 안 왔는데 왜
            위반인가" 를 화면 밖에서 묻게 되고, 그 답을 아는 사람은 코드를
            읽은 사람뿐이다.
          */}
          <p className="text-xs text-muted">{t('desk:report.breachedHint')}</p>
          <p className="text-xs text-muted">{t('desk:report.overdueHint')}</p>
        </>
      )}
    </Card>
  )
}

function AgentTable({ rows }: { rows: AgentRow[] }) {
  const { t } = useTranslation(['desk'])
  const names = useUserNames(rows.map((row) => row.assignee_id))

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold">{t('desk:report.agentsTitle')}</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:report.agentsEmpty')}</p>
      ) : (
        <>
          <table className="w-full text-sm" data-testid="desk-report-agents">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th scope="col" className="py-1 pr-3 font-normal">
                  {t('desk:report.agent')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.ticketsColumn')}
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  {t('desk:report.resolved')}
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  {t('desk:report.averageWallclock')}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.assignee_id ?? '∅'} className="border-t border-line">
                  <th scope="row" className="py-1 pr-3 text-left font-normal">
                    {/*
                      담당자 없음은 **빈 칸이 아니라 한 칸이다.** 이름을 비워
                      두면 표에 이유 없는 구멍이 생기고, 보통 가장 봐야 할
                      무리가 그 구멍에 들어간다.
                    */}
                    {row.assignee_id === null
                      ? t('desk:report.unassigned')
                      : (names.data?.get(row.assignee_id) ?? row.assignee_id)}
                  </th>
                  <td className="py-1 pr-3 text-right tabular-nums">{row.tickets}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">
                    {t('desk:report.resolvedOf', {
                      resolved: row.resolved,
                      total: row.tickets,
                    })}
                  </td>
                  <td className="py-1 text-right tabular-nums" data-testid="agent-average">
                    {/*
                      **하나도 안 끝났으면 `—` 다.** `0` 으로 두면 못 끝낸
                      사람이 가장 빠른 사람으로 보인다.
                    */}
                    {row.average_wallclock_seconds === null
                      ? t('desk:report.noAverage')
                      : humanize(row.average_wallclock_seconds, t)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted">{t('desk:report.averageWallclockHint')}</p>
        </>
      )}
    </Card>
  )
}
