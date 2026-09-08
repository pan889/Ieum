/**
 * 내 할 일 — 문서를 가로질러 모은다 (B12).
 *
 * 문서마다 태스크가 흩어져 있으면 사람은 자기 일을 찾으려고 문서를 하나씩
 * 열어야 한다. 그러면 태스크를 적는 습관 자체가 무너진다.
 *
 * **못 보는 문서의 할 일은 안 뜬다.** 서버가 스페이스 권한과 문서 제한을
 * 둘 다 본다 — 제목만으로도 새는 것이 있기 때문이다.
 *
 * 기한 있는 것이 먼저 온다(서버 정렬). 목록의 첫 줄이 가장 안 급한 일이면
 * 목록을 보는 이유가 없다.
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { RichText } from '@/shared/markdown/RichText'
import { Alert, Badge, Button, Card } from '@/shared/ui/primitives'

import { isoDay, urgencyOf, type Urgency } from './due'

const TONE: Record<Urgency, 'danger' | 'in_progress' | 'neutral'> = {
  overdue: 'danger',
  today: 'danger',
  soon: 'in_progress',
  later: 'neutral',
  none: 'neutral',
}

export function MyTasksScreen() {
  const { t } = useTranslation(['wiki', 'common'])
  const [includeDone, setIncludeDone] = useState(false)

  const tasks = useQuery({
    queryKey: ['wiki', 'tasks', 'mine', includeDone],
    queryFn: () => wikiApi.pages.myTasks({ include_done: includeDone }),
  })
  const rows = tasks.data ?? []
  const today = isoDay(new Date())

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('wiki:tasks.mineTitle')}</h1>
        <p className="text-sm text-muted">{t('wiki:tasks.mineDescription')}</p>
      </header>

      <Button
        variant="ghost"
        className="self-start text-xs"
        onClick={() => {
          setIncludeDone((on) => !on)
        }}
      >
        {t(includeDone ? 'wiki:tasks.hideDone' : 'wiki:tasks.showDone')}
      </Button>

      {tasks.isError ? <Alert>{describeError(tasks.error)}</Alert> : null}
      {tasks.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('wiki:tasks.mineEmpty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2" data-testid="my-tasks">
        {rows.map((row) => {
          const urgency = urgencyOf(row.due_date, today)
          return (
            <li key={`${row.page_id}:${row.line}`}>
              <Card className="flex flex-col gap-1">
                <span className="flex flex-wrap items-baseline gap-2 text-sm">
                  <RichText
                    source={row.text}
                    className={row.done ? 'text-muted line-through' : undefined}
                  />
                  {row.due_date === null ? null : (
                    <Badge tone={row.done ? 'neutral' : TONE[urgency]}>
                      {t(`wiki:tasks.due.${urgency}`, { date: row.due_date })}
                    </Badge>
                  )}
                </span>
                {/*
                  **어느 문서의 일인지 보인다.** 없으면 하나씩 눌러 확인해야
                  하고, 그러면 목록이 목록 구실을 못 한다.
                */}
                <Link
                  to="/wiki/$spaceKey/$"
                  params={{ spaceKey: row.space_key, _splat: row.path }}
                  className="text-xs text-accent hover:underline"
                >
                  {row.space_key} · {row.page_title}
                </Link>
              </Card>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
