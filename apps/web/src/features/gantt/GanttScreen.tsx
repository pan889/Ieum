import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { GanttConflict, GanttRow, Project } from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { ganttApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, EmptyState, Field, Select } from '@/shared/ui/primitives'

import { arrowsOf, barOf, conflictedIds, daysIn, monthSpan } from './bars'

/** 한 달 또는 두 달. 서버 상한(`MAX_DAYS` = 70)을 넘지 않는 범위다. */
const RANGES = [1, 2] as const

export function GanttScreen() {
  const { t, i18n } = useTranslation(['gantt', 'common', 'issues'])
  const [project, setProject] = useState<Project | null>(null)
  const now = new Date()
  const [cursor, setCursor] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 })
  const [months, setMonths] = useState<(typeof RANGES)[number]>(1)
  const [draft, setDraft] = useState('')
  const [iql, setIql] = useState('')

  const span = monthSpan(cursor.year, cursor.month, months)
  const { starts_on: startsOn, ends_on: endsOn } = span

  const found = useQuery({
    queryKey: ['gantt', project?.id, startsOn, endsOn, iql],
    queryFn: () =>
      ganttApi.window({
        projectId: project?.id ?? '',
        startsOn,
        endsOn,
        iql: iql || undefined,
      }),
    enabled: project !== null,
  })

  const step = (by: number) => {
    const month = cursor.month + by
    if (month < 1) setCursor({ year: cursor.year - 1, month: 12 })
    else if (month > 12) setCursor({ year: cursor.year + 1, month: 1 })
    else setCursor({ ...cursor, month })
  }

  const monthName = new Intl.DateTimeFormat(i18n.language, { month: 'long', year: 'numeric' })
  const heading = monthName.format(new Date(Date.UTC(cursor.year, cursor.month - 1, 1)))

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{t('gantt:title')}</h1>
        <div className="ml-auto">
          <ProjectPicker
            label={t('issues:list.project')}
            chosen={project}
            onPick={setProject}
          />
        </div>
      </header>

      {/* 고르기 전에도 무엇을 해야 하는지 말한다(`BoardsScreen` 과 같다). */}
      {project === null ? (
        <EmptyState
          title={t('common:state.pickProject')}
          description={t('common:state.pickProjectHint')}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-2">
            <Button variant="ghost" onClick={() => { step(-1) }}>
              ‹
            </Button>
            <span className="min-w-40 text-center font-medium">{heading}</span>
            <Button variant="ghost" onClick={() => { step(1) }}>
              ›
            </Button>
            <Select
              label={t('gantt:range')}
              className="py-1 text-xs"
              value={String(months)}
              onChange={(e) => { setMonths(Number(e.target.value) as (typeof RANGES)[number]) }}
            >
              {RANGES.map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? t('gantt:rangeMonth') : t('gantt:rangeTwoMonths')}
                </option>
              ))}
            </Select>
            <form
              className="ml-auto flex items-end gap-2"
              onSubmit={(event) => { event.preventDefault(); setIql(draft) }}
            >
              <Field
                label={t('gantt:filter')}
                hint={t('gantt:filterHint')}
                value={draft}
                onChange={(e) => { setDraft(e.target.value) }}
              />
              <Button type="submit">{t('gantt:filterApply')}</Button>
            </form>
          </div>

          {found.isPending ? (
            <p className="text-sm text-muted">{t('common:state.loading')}</p>
          ) : found.isError ? (
            <Alert>{describeError(found.error)}</Alert>
          ) : (
            <>
              {/*
                **어긋난 순서를 먼저, 눈에 띄게.** 겹친 막대만 그려 놓으면
                사람은 화살표가 있으니 순서가 지켜진다고 읽는다.
              */}
              {found.data.conflicts.length > 0 ? (
                <Conflicts conflicts={found.data.conflicts} rows={found.data.rows} />
              ) : null}
              {found.data.undated > 0 ? (
                <Card className="py-2 text-sm">
                  {t('gantt:undated', { count: found.data.undated })}
                </Card>
              ) : null}
              {found.data.links_outside > 0 ? (
                <Card className="py-2 text-sm">
                  <p>{t('gantt:linksOutside', { count: found.data.links_outside })}</p>
                  <p className="text-xs text-muted">{t('gantt:linksOutsideHint')}</p>
                </Card>
              ) : null}
              {found.data.truncated ? <Alert>{t('gantt:truncated')}</Alert> : null}

              {found.data.rows.length === 0 ? (
                <p className="text-sm text-muted">{t('gantt:empty')}</p>
              ) : (
                <Bars
                  rows={found.data.rows}
                  window={{ starts_on: found.data.starts_on, ends_on: found.data.ends_on }}
                  conflicted={conflictedIds(found.data.conflicts)}
                />
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}

function Conflicts({ conflicts, rows }: { conflicts: GanttConflict[]; rows: GanttRow[] }) {
  const { t } = useTranslation(['gantt'])
  const keyOf = new Map(rows.map((row) => [row.id, row.key]))
  return (
    <Alert>
      <p className="font-medium">{t('gantt:conflicts', { count: conflicts.length })}</p>
      <p className="mt-1 text-xs">{t('gantt:conflictsHint')}</p>
      <ul className="mt-2 flex flex-col gap-0.5 text-xs">
        {conflicts.map((conflict) => (
          <li key={`${conflict.predecessor}-${conflict.successor}`}>
            {t('gantt:conflictRow', {
              predecessor: keyOf.get(conflict.predecessor) ?? conflict.predecessor,
              successor: keyOf.get(conflict.successor) ?? conflict.successor,
              days: conflict.overlap_days,
            })}
          </li>
        ))}
      </ul>
    </Alert>
  )
}

function Bars({
  rows,
  window,
  conflicted,
}: {
  rows: GanttRow[]
  window: { starts_on: string; ends_on: string }
  conflicted: Set<string>
}) {
  const { t, i18n } = useTranslation(['gantt'])
  const total = daysIn(window)
  const dates = new Intl.DateTimeFormat(i18n.language, { month: 'numeric', day: 'numeric' })
  const day = (iso: string) => dates.format(new Date(`${iso}T00:00:00Z`))
  const arrows = arrowsOf(rows)

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] text-xs">
        <caption className="sr-only">{t('gantt:title')}</caption>
        <tbody>
          {rows.map((row) => {
            const bar = barOf(row, window)
            const isConflicted = conflicted.has(row.id)
            const dependsCount = row.depends_on.length
            return (
              <tr key={row.id} className="border-b border-border last:border-0">
                <th scope="row" className="w-56 py-1 pr-2 text-left font-normal">
                  <Link
                    to="/issues/$issueKey"
                    params={{ issueKey: row.key }}
                    className="font-mono text-accent hover:underline"
                  >
                    {row.key}
                  </Link>{' '}
                  <span className="text-muted">{row.summary}</span>
                  {dependsCount > 0 ? (
                    <span className="ml-1 text-muted">
                      · {t('gantt:dependsOn', { count: dependsCount })}
                    </span>
                  ) : null}
                  {isConflicted ? (
                    <Badge tone="danger" className="ml-1">
                      {t('gantt:conflicted')}
                    </Badge>
                  ) : null}
                </th>
                <td className="py-1">
                  <div
                    className="grid gap-px"
                    style={{ gridTemplateColumns: `repeat(${String(total)}, minmax(4px, 1fr))` }}
                  >
                    {bar === null ? null : (
                      <span
                        title={t('gantt:bar', {
                          key: row.key,
                          summary: row.summary,
                          starts: day(row.starts_on),
                          ends: day(row.ends_on),
                        })}
                        aria-label={[
                          t('gantt:bar', {
                            key: row.key,
                            summary: row.summary,
                            starts: day(row.starts_on),
                            ends: day(row.ends_on),
                          }),
                          bar.clippedLeft ? t('gantt:clippedLeft') : '',
                          bar.clippedRight ? t('gantt:clippedRight') : '',
                          isConflicted ? t('gantt:conflicted') : '',
                          row.overdue ? t('gantt:overdue') : '',
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                        style={{ gridColumn: `${String(bar.offset + 1)} / span ${String(bar.span)}` }}
                        className={[
                          'block h-4 rounded',
                          isConflicted
                            ? 'bg-danger/40'
                            : row.overdue
                              ? 'bg-danger/20'
                              : row.state_category === 'done'
                                ? 'bg-success/30'
                                : 'bg-accent/30',
                          // 잘린 쪽 모서리를 펴서 "이어진다" 를 보이게 한다.
                          bar.clippedLeft ? 'rounded-l-none' : '',
                          bar.clippedRight ? 'rounded-r-none' : '',
                        ].join(' ')}
                      />
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {/*
        화살표는 그리지 않고 **글자로 말한다.** SVG 오버레이를 표 위에 얹으면
        줄 높이가 바뀔 때마다 좌표가 어긋나고, 화면 낭독기는 그것을 못 읽는다
        — 선행이 몇 건인지는 위 줄에 이미 적혀 있다.
      */}
      <p className="sr-only">
        {arrows.map((arrow) => `${rows[arrow.from]?.key ?? ''} → ${rows[arrow.to]?.key ?? ''}`).join(', ')}
      </p>
    </div>
  )
}
