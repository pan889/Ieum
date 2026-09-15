import { useMutation, useQuery } from '@tanstack/react-query'
import type { BulkEditResult } from '@ieum/api-client'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { searchApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { saveBlob } from '@/shared/download'
import { LIST_SHORTCUTS, type ListShortcutId } from '@/shared/keys/catalog'
import { useShortcuts } from '@/shared/keys/useHotkeys'
import { Alert, Badge, Button, Card, Chip, EmptyState } from '@/shared/ui/primitives'

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

  /**
   * `j`/`k` 가 짚고 있는 줄. **초점을 실제로 옮긴다** — 색만 칠하면 화면
   * 낭독기를 쓰는 사람에게는 아무 일도 안 일어난 것이고, 목록이 길면 짚은
   * 줄이 화면 밖에 있어도 모른다. 초점을 옮기면 스크롤도 브라우저가 한다.
   */
  //
  // 목록이 바뀌면(질의·쪽 넘김·묶어 보기) 짚은 자리를 **버려야 한다.** 남겨
  // 두면 다른 이슈를 짚은 채로 `o` 를 눌러 엉뚱한 것이 열린다. effect 로
  // 되돌리는 대신 목록의 지문을 상태에 같이 넣어 **파생**으로 만든다 —
  // 그러면 되돌리는 렌더가 한 번 더 돌지 않고, 한 프레임 동안 옛 자리가
  // 짚힌 채로 그려지는 일도 없다.
  const [picked, setPicked] = useState<{ of: string; at: number }>({ of: '', at: -1 })
  const rowsRef = useRef<(HTMLTableRowElement | null)[]>([])
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

  // 화면에 그려지는 순서 그대로. 묶어 보기를 켜면 묶음 안 순서가 곧 이 순서다 —
  // `j` 가 눈에 보이는 다음 줄로 가야 하므로 원본 배열 순서로는 안 된다.
  const groups = useMemo(
    () => groupRows(results.data?.items ?? [], groupBy),
    [results.data, groupBy],
  )
  const order = useMemo(() => groups.flatMap((g) => g.rows.map((r) => r.id)), [groups])
  const orderKey = order.join(',')
  const rowAt = picked.of === orderKey ? picked.at : -1
  const keyOf = useMemo(() => {
    const map = new Map<string, string>()
    for (const group of groups) for (const row of group.rows) map.set(row.id, row.key)
    return map
  }, [groups])

  const step = useCallback(
    (by: number) => {
      setPicked((now) => {
        if (order.length === 0) return { of: orderKey, at: -1 }
        const from = now.of === orderKey ? now.at : -1
        // 아직 아무 데도 안 짚었으면 방향과 무관하게 첫 줄부터. 아래로 가려고
        // `j` 를 눌렀는데 맨 끝으로 가면 놀란다.
        if (from < 0) return { of: orderKey, at: 0 }
        const next = Math.min(Math.max(from + by, 0), order.length - 1)
        return { of: orderKey, at: next }
      })
    },
    [order.length, orderKey],
  )

  const openCursor = useCallback(() => {
    const id = order[rowAt]
    if (id === undefined) return
    const key = keyOf.get(id)
    if (key === undefined) return
    void navigate({ to: '/issues/$issueKey', params: { issueKey: key } })
  }, [rowAt, keyOf, navigate, order])

  const listHandlers: Record<ListShortcutId, () => void> = {
    listDown: () => { step(1) },
    listUp: () => { step(-1) },
    listOpen: openCursor,
  }
  useShortcuts(LIST_SHORTCUTS, listHandlers, 'keys.groupList')

  // 짚은 줄로 **초점을 옮긴다.** 스크롤은 브라우저가 알아서 한다.
  useEffect(() => {
    if (rowAt < 0) return
    rowsRef.current[rowAt]?.focus()
  }, [rowAt])



  /** CSV 는 스트리밍 응답이라 fetch 로 받아 blob 으로 저장한다. */
  const exporting = useMutation({
    mutationFn: () => searchApi.exportCsv(activeIql),
    onSuccess: (blob) => {
      saveBlob(blob, `ieum-issues-${new Date().toISOString().slice(0, 10)}.csv`)
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
        <h1 className="text-xl font-semibold text-fg">{t('issues:list.title')}</h1>
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
          {/* 이 화면의 **주 행동**이다. 단추처럼 보여야 하고, 다른 화면의
              주 단추와 크기가 같아야 한다 — 여기만 손으로 여백을 적어 두면
              화면을 옮길 때마다 단추가 조금씩 커졌다 작아진다. */}
          <Link
            to="/issues/new"
            className="inline-flex h-8 shrink-0 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-fg shadow-raised hover:brightness-110"
          >
            {t('issues:create.title')}
          </Link>
        </div>
      </header>

      {/*
        **조작은 한 덩어리다.** 필터·저장 필터·보는 방식이 각자 배경 위에
        떠 있었고, 표가 시작되기 전까지 3백 픽셀이 맨 화면이었다 — 무엇이
        조작이고 무엇이 내용인지 경계가 없으니 화면이 짜여 있다는 느낌
        자체가 없었다. 가라앉은 한 상자에 담고 안에서 줄로 나눈다.
      */}
      <div className="divide-y divide-border rounded-card border border-border bg-sunken">
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

        {/* 보는 방식(칸·묶음)은 필터가 아니지만 **같은 종류의 조작**이다. */}
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
          <span className="w-[4.5rem] shrink-0 text-2xs font-semibold uppercase tracking-wide text-subtle">
            {t('issues:list.columns')}
          </span>
          {COLUMNS.map((id) => (
            <Chip key={id} pressed={columns.includes(id)} onClick={() => { toggleColumn(id); }}>
              {t(`issues:list.column.${id}`)}
            </Chip>
          ))}

          <span className="ml-4 shrink-0 text-2xs font-semibold uppercase tracking-wide text-subtle">
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
        <EmptyState
          title={t('issues:list.empty')}
          description={t('issues:list.emptyHint')}
        />
      ) : (
        <>
          <p className="text-xs tabular-nums text-subtle">
            {t('issues:list.count', { count: results.data.items.length })}
          </p>
          <div className="overflow-x-auto rounded-card border border-border bg-surface shadow-raised">
            <table className="w-full border-collapse text-sm">
              <thead>
                {/*
                  표 머리는 **가라앉은 면**이다. 전에는 본문과 거의 같은 흰
                  색이라, 스크롤을 조금만 내려도 어디까지가 머리인지 몰랐다.
                */}
                <tr className="border-b border-border bg-sunken text-left">
                  {/* 표의 고르기 칸은 `Checkbox` 를 쓰지 않는다. 프리미티브는
                      보이는 라벨을 요구하지만 이 칸에는 체크박스 하나 만큼의
                      너비밖에 없다. 대신 `aria-label` 로 이름을 준다 — 힌트를
                      `<label>` 에 넣는 실수가 애초에 생기지 않는 모양이다. */}
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
                    <th
                      key={id}
                      scope="col"
                      className="whitespace-nowrap px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-subtle"
                    >
                      {t(`issues:list.column.${id}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <Fragment key={group.key || '__all__'}>
                    {groupBy === 'none' ? null : (
                      <tr className="border-b border-border bg-surface-raised">
                        <th
                          scope="colgroup"
                          colSpan={columns.length + 1}
                          className="px-3 py-1.5 text-left text-xs font-semibold text-fg"
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
                  const at = order.indexOf(issue.id)
                  return (
                    <tr
                      key={issue.id}
                      ref={(el) => {
                        rowsRef.current[at] = el
                      }}
                      // 초점을 받을 수 있게 하되 Tab 순서에는 넣지 않는다 —
                      // Tab 으로 줄을 하나씩 지나가게 하면 그게 더 느리다.
                      tabIndex={-1}
                      aria-selected={at === rowAt}
                      onFocus={() => {
                        setPicked({ of: orderKey, at })
                      }}
                      // 짚은 줄은 **단색 토큰**으로 칠한다. `accent/10` 은
                      // 투명도라, 줄무늬나 그룹 머리 위에 겹치면 색이 달라졌다.
                      className={
                        at === rowAt
                          ? 'border-b border-border bg-accent-soft outline-none last:border-0'
                          : 'border-b border-border outline-none last:border-0 hover:bg-surface-raised'
                      }
                    >
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
                        // 키·상태·우선순위·날짜는 **내용만큼만** 넓다.
                        // 전에는 모든 칸이 똑같이 늘어나서, `WEB-1` 한 칸이
                        // 160px 을 먹고 요약은 잘렸다.
                        <td
                          key={id}
                          className={
                            id === 'summary'
                              ? 'w-full px-3 py-2 align-top'
                              : 'whitespace-nowrap px-3 py-2 align-top'
                          }
                        >
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
                              <span className="text-subtle">{t('issues:detail.unassigned')}</span>
                            )
                          ) : id === 'priority' ? (
                            priorityLabel(issue.priority)
                          ) : id === 'due' ? (
                            (formatDate(issue.due_date) || <span className="text-subtle">—</span>)
                          ) : (
                            <span className="text-subtle">{formatRelative(issue.updated_at)}</span>
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
