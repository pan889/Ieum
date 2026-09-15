import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CalendarEntry, CalendarSprint, Project } from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { calendarApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, EmptyState, Field } from '@/shared/ui/primitives'

import { WEEK_STARTS_ON, isoOf, monthWindow, stackIn, weeksOf } from './grid'

/** 0~6 을 격자 열 이름으로. Tailwind 는 문자열을 조립해 만든 클래스를 못 본다. */
const COL_START = ['col-start-1', 'col-start-2', 'col-start-3', 'col-start-4', 'col-start-5', 'col-start-6', 'col-start-7'] as const
const COL_SPAN = ['', 'col-span-1', 'col-span-2', 'col-span-3', 'col-span-4', 'col-span-5', 'col-span-6', 'col-span-7'] as const

function todayIso(): string {
  return isoOf(Math.floor(Date.now() / 86_400_000))
}

export function CalendarScreen() {
  const { t } = useTranslation(['calendar', 'common', 'issues'])
  const [project, setProject] = useState<Project | null>(null)
  const now = new Date()
  const [cursor, setCursor] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 })
  const [draft, setDraft] = useState('')
  const [iql, setIql] = useState('')

  const window = monthWindow(cursor.year, cursor.month)
  const weeks = weeksOf(window)
  const today = todayIso()

  const found = useQuery({
    queryKey: ['calendar', project?.id, window.startsOn, window.endsOn, iql],
    queryFn: () =>
      calendarApi.window({
        projectId: project?.id ?? '',
        startsOn: window.startsOn,
        endsOn: window.endsOn,
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

  const monthName = new Intl.DateTimeFormat(useTranslation().i18n.language, { month: 'long' })
  const heading = t('calendar:month', {
    year: cursor.year,
    month: monthName.format(new Date(Date.UTC(cursor.year, cursor.month - 1, 1))),
  })

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{t('calendar:title')}</h1>
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
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" onClick={() => { step(-1) }}>
              {t('calendar:prev')}
            </Button>
            <span className="min-w-40 text-center font-medium">{heading}</span>
            <Button variant="ghost" onClick={() => { step(1) }}>
              {t('calendar:next')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => { setCursor({ year: now.getFullYear(), month: now.getMonth() + 1 }) }}
            >
              {t('calendar:today')}
            </Button>
            <form
              className="ml-auto flex items-end gap-2"
              onSubmit={(event) => { event.preventDefault(); setIql(draft) }}
            >
              <Field
                label={t('calendar:filter')}
                hint={t('calendar:filterHint')}
                value={draft}
                onChange={(e) => { setDraft(e.target.value) }}
              />
              <Button type="submit">{t('calendar:filterApply')}</Button>
            </form>
          </div>

          {found.isPending ? (
            <p className="text-sm text-muted">{t('common:state.loading')}</p>
          ) : found.isError ? (
            <Alert>{describeError(found.error)}</Alert>
          ) : (
            <>
              {/*
                **놓지 못한 것을 적는다.** 조용히 빼면 사람은 "이번 달은
                한가하다" 고 읽는데, 실제로는 날짜만 안 적힌 일이 스무 건 있다.
              */}
              {found.data.undated > 0 ? (
                <Card className="py-2 text-sm">
                  <p>{t('calendar:undated', { count: found.data.undated })}</p>
                  <p className="text-xs text-muted">{t('calendar:undatedHint')}</p>
                </Card>
              ) : null}
              {found.data.truncated ? <Alert>{t('calendar:truncated')}</Alert> : null}
              {found.data.sprints.length > 0 ? (
                <SprintBands sprints={found.data.sprints} />
              ) : null}

              <div className="grid grid-cols-7 gap-px rounded-md border border-border bg-border text-xs">
                {Array.from({ length: 7 }, (_, i) => (
                  <div key={i} className="bg-surface px-2 py-1 text-center font-medium text-muted">
                    {t(`calendar:weekday.${String((WEEK_STARTS_ON + i) % 7)}`)}
                  </div>
                ))}
              </div>

              {weeks.map((week) => (
                <Week
                  key={week[0]}
                  week={week}
                  today={today}
                  month={cursor.month}
                  entries={found.data.entries}
                />
              ))}

              {found.data.entries.length === 0 ? (
                <p className="text-sm text-muted">{t('calendar:empty')}</p>
              ) : null}
            </>
          )}
        </>
      )}
    </section>
  )
}

/** 달력 위의 스프린트 띠. 격자 안에 넣지 않고 위에 따로 적는다 —
 * 날짜 경계가 보는 사람 타임존에 달려 있어서 칸에 정확히 맞출 수 없다. */
function SprintBands({ sprints }: { sprints: CalendarSprint[] }) {
  const { t, i18n } = useTranslation(['calendar', 'sprints'])
  const dates = new Intl.DateTimeFormat(i18n.language, { month: 'numeric', day: 'numeric' })
  const day = (iso: string | null) => (iso === null ? '' : dates.format(new Date(iso)))
  return (
    <ul className="flex flex-wrap gap-2 text-xs">
      {sprints.map((sprint) => (
        <li key={sprint.id}>
          <Badge tone={sprint.state === 'active' ? 'in_progress' : 'neutral'}>
            {t('calendar:sprintBand', {
              name: sprint.name,
              state: t(`sprints:state.${sprint.state}`),
            })}{' '}
            {day(sprint.starts_at)}–{day(sprint.ends_at)}
          </Badge>
        </li>
      ))}
    </ul>
  )
}

function Week({
  week,
  today,
  month,
  entries,
}: {
  week: string[]
  today: string
  month: number
  entries: CalendarEntry[]
}) {
  const { t } = useTranslation(['calendar'])
  const rows = stackIn(entries, week)

  return (
    <div className="rounded-md border border-border">
      <div className="grid grid-cols-7 gap-px bg-border text-xs">
        {week.map((iso) => (
          <div
            key={iso}
            className={
              'bg-surface px-2 py-1 ' +
              // 이 달이 아닌 날은 흐리게. 격자를 주 경계까지 넓혔기 때문에
              // 앞뒤 며칠이 늘 섞여 있다.
              (Number(iso.slice(5, 7)) === month ? 'text-fg' : 'text-muted')
            }
          >
            <span className={iso === today ? 'rounded bg-accent px-1 text-accent-fg' : undefined}>
              {Number(iso.slice(8, 10))}
            </span>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-0.5 p-1">
        {rows.map((row, index) => (
          <div key={index} className="grid grid-cols-7 gap-0.5">
            {row.map((item) => (
              <Link
                key={item.id}
                to="/issues/$issueKey"
                params={{ issueKey: item.key }}
                title={t('calendar:entry', { key: item.key, summary: item.summary })}
                className={[
                  COL_START[item.offset] ?? 'col-start-1',
                  COL_SPAN[item.span] ?? 'col-span-1',
                  'truncate rounded px-1.5 py-0.5 text-xs',
                  item.overdue
                    ? 'bg-danger/15 text-danger'
                    : item.state_category === 'done'
                      ? 'bg-success/15 text-success'
                      : 'bg-accent/15 text-accent',
                  // 잘린 쪽은 모서리를 펴서 "이어진다" 를 보이게 한다.
                  item.continuesLeft ? 'rounded-l-none' : '',
                  item.continuesRight ? 'rounded-r-none' : '',
                ].join(' ')}
              >
                <span className="font-mono">{item.key}</span> {item.summary}
                {item.overdue ? (
                  <span className="ml-1 font-medium">· {t('calendar:overdue')}</span>
                ) : null}
              </Link>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
