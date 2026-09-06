import { useMutation, useQuery } from '@tanstack/react-query'
import type { BulkEditResult } from '@ieum/api-client'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { Fragment, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { searchApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Chip } from '@/shared/ui/primitives'

import { BulkBar } from './BulkBar'
import { FilterBar } from './FilterBar'
import { SavedFilters } from './SavedFilters'
import { formatDate, formatRelative, priorityLabel, categoryTone } from './format'
import { useUserNames } from './hooks'
import type { GroupField, IssueGroup } from './grouping'
import { GROUP_FIELDS, groupRows } from './grouping'
import { toIql } from './iql'
import type { IssuesSearch } from './urlState'
import { isIqlMode, toFilters, toIqlSearch, toSearch } from './urlState'

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
  const navigate = useNavigate()
  // 필터의 주인은 URL 이다 — 새로고침·뒤로가기·링크 공유가 같은 경로를 탄다.
  const search = useSearch({ from: '/issues' })

  const [columns, setColumns] = useState<ColumnId[]>(loadColumns)
  const [cursors, setCursors] = useState<string[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [bulkResult, setBulkResult] = useState<BulkEditResult | null>(null)
  /**
   * IQL 상자에 타이핑 중인 내용. URL 에 넣지 않는다 — 글자마다 히스토리가
   * 쌓이고 아직 실행하지도 않은 질의가 링크에 실린다. 실행할 때 URL 로 간다.
   */
  const [typing, setTyping] = useState<string | null>(null)
  //: 그룹화는 URL 에 싣지 않는다. 질의가 아니라 보는 방식이다 — 컬럼 선택과 같다.
  const [groupBy, setGroupBy] = useState<GroupField>('none')

  const filters = toFilters(search)
  const iqlMode = isIqlMode(search)
  // 칩 모드면 칩이 진실이고, IQL 모드면 URL 의 `iql` 이 진실이다.
  const activeIql = iqlMode ? (search.iql ?? '') : toIql(filters)
  // 상자에 보이는 값: 타이핑 중이면 그것, 아니면 URL 의 질의.
  const iqlDraft = typing ?? (iqlMode ? activeIql : null)
  const cursor = cursors.at(-1)

  const go = (next: IssuesSearch) => {
    setCursors([])
    setSelected([])
    setBulkResult(null)
    // replace 다. 칩 하나 누를 때마다 히스토리가 쌓이면 뒤로가기가 못 쓴다.
    void navigate({ to: '/issues', search: next, replace: true })
  }

  const results = useQuery({
    queryKey: ['issues', 'search', activeIql, cursor],
    queryFn: () => searchApi.search({ iql: activeIql, limit: 50, ...(cursor ? { cursor } : {}) }),
  })

  const names = useUserNames((results.data?.items ?? []).map((i) => i.assignee_id))

  /**
   * CSV 는 스트리밍 응답이라 fetch 로 받아 blob 으로 저장한다.
   * `<a href>` 로 걸 수 없다 — 액세스 토큰이 메모리에만 있어 앵커 클릭에는
   * Authorization 헤더가 안 붙는다 (첨부 다운로드와 같은 이유).
   */
  const exporting = useMutation({
    mutationFn: () => searchApi.exportCsv(activeIql),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `ieum-issues-${new Date().toISOString().slice(0, 10)}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    },
  })

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

  return (
    <section className="mx-auto flex max-w-6xl flex-col gap-5">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t('issues:list.title')}</h1>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            loading={exporting.isPending}
            disabled={activeIql.trim() === ''}
            title={activeIql.trim() === '' ? t('issues:export.needsFilter') : undefined}
            onClick={() => { exporting.mutate(); }}
          >
            {t('issues:export.csv')}
          </Button>
          <Link
            to="/issues/new"
            className="rounded-md bg-accent px-3.5 py-2 text-sm font-medium text-accent-fg"
          >
            {t('issues:create.title')}
          </Link>
        </div>
      </header>

      <FilterBar
        filters={filters}
        onFiltersChange={(next) => { setTyping(null); go(toSearch(next)) }}
        iqlDraft={iqlDraft}
        onIqlDraftChange={(next) => {
          setTyping(next)
          // 칩으로 돌아가면 URL 도 칩 모드로 되돌린다.
          if (next === null) go(toSearch(filters))
        }}
        onRun={(iql) => { setTyping(null); go(toIqlSearch(iql, filters)) }}
        invalid={results.isError ? describeError(results.error) : undefined}
      />

      <SavedFilters
        activeIql={activeIql}
        onLoad={(iql) => { setTyping(null); go(toIqlSearch(iql)) }}
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

        <span className="ml-4 shrink-0 text-xs font-medium text-muted">
          {t('issues:list.groupBy')}
        </span>
        {GROUP_FIELDS.map((field) => (
          <Chip
            key={field}
            pressed={groupBy === field}
            onClick={() => { setGroupBy(field) }}
          >
            {t(`issues:list.group.${field}`)}
          </Chip>
        ))}
      </div>

      {selected.length > 0 ? (
        <BulkBar
          selected={selected}
          onClear={() => { setSelected([]); }}
          onApplied={(result) => {
            setBulkResult(result)
            setSelected([])
            void results.refetch()
          }}
        />
      ) : null}

      {/* 선택이 풀려도 결과는 남는다. 실패 목록이 같이 사라지면 100건 중
          3건이 실패해도 사용자는 모른다. */}
      {bulkResult ? (
        <Card className="flex flex-col gap-1 py-3 text-sm">
          <div className="flex items-center gap-3">
            <span>{t('issues:bulk.done', { count: bulkResult.updated.length })}</span>
            <Button
              variant="ghost"
              className="ml-auto text-xs"
              onClick={() => { setBulkResult(null); }}
            >
              {t('common:action.close')}
            </Button>
          </div>
          {bulkResult.failed.length > 0 ? (
            <Alert>
              {t('issues:bulk.someFailed', { count: bulkResult.failed.length })}
              <ul className="mt-1 list-disc pl-4 text-xs">
                {bulkResult.failed.slice(0, 5).map((failure) => (
                  <li key={failure.issue_id}>{failure.message}</li>
                ))}
              </ul>
            </Alert>
          ) : null}
        </Card>
      ) : null}

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
                  <th scope="col" className="w-8 px-2">
                    <input
                      type="checkbox"
                      aria-label={t('issues:bulk.selectAll')}
                      checked={
                        results.data.items.length > 0 &&
                        selected.length === results.data.items.length
                      }
                      onChange={(event) => {
                        setSelected(
                          event.target.checked ? results.data.items.map((i) => i.id) : [],
                        )
                      }}
                    />
                  </th>
                  {columns.map((id) => (
                    <th key={id} scope="col" className="px-3 py-2 text-xs font-medium text-muted">
                      {t(`issues:list.column.${id}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groupRows(results.data.items, groupBy).map((group) => (
                  <Fragment key={group.key || '__all__'}>
                    {groupBy === 'none' ? null : (
                      <tr className="border-b border-border bg-surface-raised">
                        <th
                          scope="colgroup"
                          colSpan={columns.length + 1}
                          className="px-3 py-1.5 text-left text-xs font-medium"
                        >
                          {groupLabel(group, groupBy, t, names.data)}
                          {/* 이 페이지에 이만큼. 전체 개수가 아니다 — 서버가
                              그룹별로 세어 주지 않는다. */}
                          <span className="ml-2 font-normal text-muted">{group.rows.length}</span>
                        </th>
                      </tr>
                    )}
                    {group.rows.map((issue) => {
                  const key = issue.key
                  return (
                    <tr key={issue.id} className="border-b border-border last:border-0">
                      <td className="px-2 align-top">
                        <input
                          type="checkbox"
                          aria-label={t('issues:bulk.select')}
                          checked={selected.includes(issue.id)}
                          onChange={(event) => {
                            setSelected((current) =>
                              event.target.checked
                                ? [...current, issue.id]
                                : current.filter((id) => id !== issue.id),
                            )
                          }}
                        />
                      </td>
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
                  </Fragment>
                ))}
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

/**
 * 그룹 머리글 문구.
 *
 * 서버가 이름을 주는 건 상태뿐이다. 담당자는 id 만 오므로 목록이 이미
 * 조회해 둔 이름 맵을 쓰고, 우선순위는 번역이 필요하다.
 */
function groupLabel(
  group: IssueGroup,
  field: GroupField,
  t: TFunction<['issues', 'common']>,
  names: Map<string, string> | undefined,
): string {
  if (field === 'priority') return priorityLabel(Number(group.key))
  if (field === 'assignee') {
    if (group.key === '') return t('issues:detail.unassigned')
    return names?.get(group.key) ?? '…'
  }
  return group.label || group.key
}
