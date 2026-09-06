import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { searchApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Chip } from '@/shared/ui/primitives'

import { FilterBar } from './FilterBar'
import { formatDate, formatRelative, priorityLabel, categoryTone } from './format'
import { useUserNames } from './hooks'
import type { IssueFilters } from './iql'
import { EMPTY_FILTERS, toIql } from './iql'

const COLUMNS = ['key', 'summary', 'status', 'assignee', 'priority', 'due', 'updated'] as const
type ColumnId = (typeof COLUMNS)[number]

const DEFAULT_COLUMNS: ColumnId[] = ['key', 'summary', 'status', 'assignee', 'priority', 'updated']
const COLUMN_STORAGE_KEY = 'ieum.issues.columns'

function loadColumns(): ColumnId[] {
  try {
    const raw = localStorage.getItem(COLUMN_STORAGE_KEY)
    if (!raw) return DEFAULT_COLUMNS
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return DEFAULT_COLUMNS
    const valid = parsed.filter((c): c is ColumnId =>
      (COLUMNS as readonly string[]).includes(c as string),
    )
    // key 와 summary 가 없으면 행을 식별할 수 없다. 저장값이 망가져도 살려 둔다.
    return valid.length > 0 ? valid : DEFAULT_COLUMNS
  } catch {
    return DEFAULT_COLUMNS
  }
}

export function IssuesScreen() {
  const { t } = useTranslation(['issues', 'common'])
  const [filters, setFilters] = useState<IssueFilters>(EMPTY_FILTERS)
  const [iqlDraft, setIqlDraft] = useState<string | null>(null)
  const [ranIql, setRanIql] = useState<string | null>(null)
  const [columns, setColumns] = useState<ColumnId[]>(loadColumns)
  const [cursors, setCursors] = useState<string[]>([])

  // 칩 모드면 칩이 진실이고, IQL 모드면 마지막으로 "실행" 한 질의가 진실이다.
  const activeIql = iqlDraft === null ? toIql(filters) : (ranIql ?? toIql(filters))
  const cursor = cursors.at(-1)

  const results = useQuery({
    queryKey: ['issues', 'search', activeIql, cursor],
    queryFn: () => searchApi.search({ iql: activeIql, limit: 50, ...(cursor ? { cursor } : {}) }),
  })

  const names = useUserNames((results.data?.items ?? []).map((i) => i.assignee_id))

  const toggleColumn = (id: ColumnId) => {
    const next = columns.includes(id) ? columns.filter((c) => c !== id) : [...COLUMNS].filter(
      (c) => columns.includes(c) || c === id,
    )
    if (next.length === 0) return
    setColumns(next)
    try {
      localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* 프라이빗 모드 */
    }
  }

  const resetPaging = () => { setCursors([]); }

  return (
    <section className="mx-auto flex max-w-6xl flex-col gap-5">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t('issues:list.title')}</h1>
        <Link
          to="/issues/new"
          className="rounded-md bg-accent px-3.5 py-2 text-sm font-medium text-accent-fg"
        >
          {t('issues:create.title')}
        </Link>
      </header>

      <FilterBar
        filters={filters}
        onFiltersChange={(next) => { setFilters(next); resetPaging(); }}
        iqlDraft={iqlDraft}
        onIqlDraftChange={(next) => {
          setIqlDraft(next)
          if (next === null) { setRanIql(null); resetPaging(); }
        }}
        onRun={(iql) => { setRanIql(iql); resetPaging(); }}
        invalid={results.isError ? describeError(results.error) : undefined}
      />

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="w-16 shrink-0 text-xs font-medium text-muted">
          {t('issues:list.columns')}
        </span>
        {COLUMNS.map((id) => (
          <Chip key={id} pressed={columns.includes(id)} onClick={() => { toggleColumn(id); }}>
            {t(`issues:list.column.${id}`)}
          </Chip>
        ))}
      </div>

      {results.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : results.isError ? (
        <Alert>{describeError(results.error)}</Alert>
      ) : results.data.items.length === 0 ? (
        <Card className="text-center">
          <p className="font-medium">{t('issues:list.empty')}</p>
          <p className="mt-1 text-sm text-muted">{t('issues:list.emptyHint')}</p>
        </Card>
      ) : (
        <>
          <p className="text-xs text-muted">
            {t('issues:list.count', { count: results.data.items.length })}
          </p>
          <div className="overflow-x-auto rounded-card border border-border">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-raised text-left">
                  {columns.map((id) => (
                    <th key={id} scope="col" className="px-3 py-2 text-xs font-medium text-muted">
                      {t(`issues:list.column.${id}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.data.items.map((issue) => {
                  const key = issue.key
                  return (
                    <tr key={issue.id} className="border-b border-border last:border-0">
                      {columns.map((id) => (
                        <td key={id} className="px-3 py-2 align-top">
                          {id === 'key' ? (
                            <Link
                              to="/issues/$issueKey"
                              params={{ issueKey: key }}
                              className="font-mono text-xs text-accent hover:underline"
                            >
                              {key}
                            </Link>
                          ) : id === 'summary' ? (
                            <Link
                              to="/issues/$issueKey"
                              params={{ issueKey: key }}
                              className="hover:underline"
                            >
                              {issue.summary}
                            </Link>
                          ) : id === 'status' ? (
                            <Badge tone={categoryTone(issue.state_category)}>
                              {issue.state_name}
                            </Badge>
                          ) : id === 'assignee' ? (
                            issue.assignee_id ? (
                              (names.data?.get(issue.assignee_id) ?? '…')
                            ) : (
                              <span className="text-muted">{t('issues:detail.unassigned')}</span>
                            )
                          ) : id === 'priority' ? (
                            priorityLabel(issue.priority)
                          ) : id === 'due' ? (
                            (formatDate(issue.due_date) || <span className="text-muted">—</span>)
                          ) : (
                            <span className="text-muted">{formatRelative(issue.updated_at)}</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex justify-center gap-2">
            {cursors.length > 0 ? (
              <Button
                variant="secondary"
                onClick={() => { setCursors((c) => c.slice(0, -1)); }}
              >
                ←
              </Button>
            ) : null}
            {results.data.next_cursor ? (
              <Button
                variant="secondary"
                onClick={() => {
                  const next = results.data.next_cursor
                  if (next) setCursors((c) => [...c, next])
                }}
              >
                {t('issues:list.loadMore')}
              </Button>
            ) : null}
          </div>
        </>
      )}
    </section>
  )
}
