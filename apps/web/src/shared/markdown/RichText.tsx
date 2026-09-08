/**
 * 디렉티브까지 그리는 본문 (wiki-markdown.md 3절).
 *
 * `Markdown` 은 마크다운을 HTML 로만 바꾼다. `::children`·`::issues` 는
 * 서버에서 데이터를 받아야 해서 그 HTML 안에 섞을 수 없다 — 그래서 문서를
 * 조각으로 나누고, 디렉티브 자리에는 컴포넌트를 그린다.
 *
 * 제목 `id` 의 중복 번호는 문서 전체에서 하나로 센다. 조각마다 새로 세면
 * `::toc` 링크가 어긋나므로, 렌더는 **한 memo 안에서** 순서대로 한다.
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { createContext, useContext, useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import type { PageNode } from '@ieum/api-client'
import { reportsApi, searchApi, usersApi, wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { CountBars } from '@/shared/ui/CountBars'

import { MarkdownHtml } from './Markdown'
import { renderMarkdown } from './dialect'
import { collectHeadings, firstParagraph, splitDirectives } from './directives'
import type { Heading, Segment } from './directives'

/**
 * 문서가 놓인 자리. `::children` 은 이게 있어야 뜻이 있다.
 *
 * 없으면(이슈 설명·코멘트) 그 디렉티브는 "여기서는 쓸 수 없다"고 말한다.
 * 조용히 비워 두면 왜 안 나오는지 알 수 없다.
 */
export interface DocumentPlace {
  spaceKey: string
  /** 지금 문서의 경로. 하위를 여기서부터 찾는다. */
  path: string
  nodes: PageNode[]
}

const PlaceContext = createContext<DocumentPlace | null>(null)

export function DocumentPlaceProvider({
  place,
  children,
}: {
  place: DocumentPlace
  children: React.ReactNode
}) {
  return <PlaceContext.Provider value={place}>{children}</PlaceContext.Provider>
}

interface Rendered {
  segment: Segment
  /** 마크다운 조각이거나 상자 안쪽. 리프 디렉티브면 null. */
  html: string | null
}

export function RichText({
  source,
  className,
}: {
  source: string
  className?: string | undefined
}) {
  const parts = useMemo<Rendered[]>(() => {
    // 문서 하나당 하나. memo 안에서 만들어야 다시 그릴 때 두 번 세지 않는다.
    const slugs = new Map<string, number>()
    return splitDirectives(source).map((segment) => ({
      segment,
      html:
        segment.kind === 'markdown'
          ? renderMarkdown(segment.text, slugs)
          : segment.body !== null
            ? renderMarkdown(segment.body, slugs)
            : null,
    }))
  }, [source])

  return (
    <div className={className}>
      {parts.map((part, index) => (
        <Part key={index} part={part} source={source} />
      ))}
    </div>
  )
}

function Part({ part, source }: { part: Rendered; source: string }) {
  const { segment, html } = part
  if (segment.kind === 'markdown') return <MarkdownHtml html={html ?? ''} />
  if (segment.body !== null) {
    return <Admonition tone={segment.name} title={segment.attrs['title']} html={html ?? ''} />
  }
  if (segment.name === 'toc') return <Toc source={source} depth={depthOf(segment.attrs, 3)} />
  if (segment.name === 'children') return <Children depth={depthOf(segment.attrs, 1)} />
  if (segment.name === 'excerpt') return <Excerpt path={segment.attrs['page'] ?? ''} />
  if (segment.name === 'chart') return <Chart attrs={segment.attrs} />
  return <Issues attrs={segment.attrs} />
}

function depthOf(attrs: Record<string, string>, fallback: number): number {
  const raw = attrs['depth']
  const value = raw ? Number(raw) : NaN
  return Number.isInteger(value) && value >= 1 && value <= 6 ? value : fallback
}

function Admonition({
  tone,
  title,
  html,
}: {
  tone: string
  title?: string | undefined
  html: string
}) {
  const { t } = useTranslation(['markdown'])
  return (
    <aside className={`ieum-admonition ieum-admonition-${tone}`}>
      <p className="ieum-admonition-title">{title ?? t(`markdown:admonition.${tone}`)}</p>
      <MarkdownHtml html={html} />
    </aside>
  )
}

function Toc({ source, depth }: { source: string; depth: number }) {
  const { t } = useTranslation(['markdown'])
  const headings = useMemo(() => collectHeadings(source, depth), [source, depth])
  if (headings.length === 0) {
    return <DirectiveNote>{t('markdown:toc.empty')}</DirectiveNote>
  }
  // 제일 얕은 제목을 기준으로 들여쓴다. `##` 부터 시작하는 문서가 흔하다.
  const top = Math.min(...headings.map((h) => h.level))
  return (
    <nav className="ieum-directive" aria-label={t('markdown:toc.label')}>
      {/* 이름표가 없으면 링크 세 줄이 든 상자가 무엇인지 알 수 없다. */}
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
        {t('markdown:toc.label')}
      </p>
      <ol className="flex flex-col gap-0.5 text-sm">
        {headings.map((heading: Heading, index) => (
          <li key={`${heading.slug}-${String(index)}`} style={{ paddingLeft: `${String((heading.level - top) * 0.9)}rem` }}>
            <a href={`#${heading.slug}`} className="text-accent hover:underline">
              {heading.text}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  )
}

function Children({ depth }: { depth: number }) {
  const { t } = useTranslation(['markdown'])
  const place = useContext(PlaceContext)
  if (!place) return <DirectiveNote>{t('markdown:children.needsPage')}</DirectiveNote>

  const prefix = place.path ? `${place.path}/` : ''
  const rows = place.nodes
    .filter((node) => node.path.startsWith(prefix) && node.path !== place.path)
    .filter((node) => node.path.slice(prefix.length).split('/').length <= depth)
    .sort((a, b) => a.path.localeCompare(b.path))

  if (rows.length === 0) return <DirectiveNote>{t('markdown:children.empty')}</DirectiveNote>
  return (
    <nav className="ieum-directive" aria-label={t('markdown:children.label')}>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
        {t('markdown:children.label')}
      </p>
      <ul className="flex flex-col gap-0.5 text-sm">
        {rows.map((node) => (
          <li
            key={node.id}
            style={{
              paddingLeft: `${String((node.path.slice(prefix.length).split('/').length - 1) * 0.9)}rem`,
            }}
          >
            <Link
              to="/wiki/$spaceKey/$"
              params={{ spaceKey: place.spaceKey, _splat: node.path }}
              className="text-accent hover:underline"
            >
              {node.title}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  )
}

const DEFAULT_COLUMNS = ['key', 'summary', 'status'] as const
const MAX_ROWS = 100

function Issues({ attrs }: { attrs: Record<string, string> }) {
  const { t } = useTranslation(['markdown'])
  const iql = (attrs['query'] ?? '').trim()
  const columns = (attrs['columns'] ?? '')
    .split(',')
    .map((c) => c.trim().toLowerCase())
    .filter(Boolean)
  const shown = columns.length > 0 ? columns : [...DEFAULT_COLUMNS]
  const limit = clampLimit(attrs['limit'])

  const result = useQuery({
    queryKey: ['markdown', 'issues', iql, limit],
    queryFn: () => searchApi.search({ iql, limit }),
    enabled: iql !== '',
    staleTime: 30_000,
  })

  const names = useNames((result.data?.items ?? []).map((issue) => issue.assignee_id))

  if (iql === '') return <DirectiveNote>{t('markdown:issues.needsQuery')}</DirectiveNote>
  if (result.isPending) return <DirectiveNote>{t('markdown:issues.loading')}</DirectiveNote>
  // 질의가 틀렸으면 여기서 말한다. 서버가 문법을 판정하는 유일한 곳이다.
  if (result.isError) return <DirectiveNote tone="error">{describeError(result.error)}</DirectiveNote>
  if (result.data.items.length === 0) return <DirectiveNote>{t('markdown:issues.empty')}</DirectiveNote>

  return (
    <div className="ieum-directive overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted">
            {shown.map((column) => (
              <th key={column} className="py-1 pr-3 font-medium">
                {t(`markdown:issues.column.${column}`, { defaultValue: column })}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((issue) => (
            <tr key={issue.id} className="border-b border-border/50 last:border-0">
              {shown.map((column) => (
                <td key={column} className="py-1 pr-3 align-top">
                  <IssueCell column={column} issue={issue} names={names} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.data.next_cursor ? (
        <p className="pt-1 text-xs text-muted">{t('markdown:issues.more', { count: limit })}</p>
      ) : null}
    </div>
  )
}

function clampLimit(raw: string | undefined): number {
  const value = raw ? Number(raw) : NaN
  if (!Number.isInteger(value) || value < 1) return 20
  return Math.min(value, MAX_ROWS)
}

function IssueCell({
  column,
  issue,
  names,
}: {
  column: string
  issue: IssueRow
  names: ReadonlyMap<string, string>
}) {
  if (column === 'key') {
    return (
      <Link
        to="/issues/$issueKey"
        params={{ issueKey: issue.key }}
        className="font-mono text-xs text-accent hover:underline"
      >
        {issue.key}
      </Link>
    )
  }
  if (column === 'summary') return <span>{issue.summary}</span>
  if (column === 'status') return <span className="text-muted">{issue.state_name}</span>
  if (column === 'priority') return <span className="text-muted">{issue.priority}</span>
  if (column === 'due_date') return <span className="text-muted">{issue.due_date ?? '—'}</span>
  if (column === 'updated') {
    return <span className="text-muted">{issue.updated_at.slice(0, 10)}</span>
  }
  const assignee = issue.assignee_id
  return (
    <span className="text-muted">{assignee === null ? '—' : (names.get(assignee) ?? '…')}</span>
  )
}

interface IssueRow {
  key: string
  summary: string
  state_name: string
  assignee_id: string | null
  priority: number
  due_date: string | null
  updated_at: string
}

const NO_NAMES: ReadonlyMap<string, string> = new Map()

/**
 * UUID 를 사람 이름으로. **한 번에 받는다** — 행마다 부르면 20행에 20번 나간다.
 *
 * `null` 을 받아 주는 이유: 담당자 없는 이슈와 "담당자 없음" 칸이 둘 다 그
 * 모양으로 온다. 부르는 쪽이 걸러 내게 하면 두 자리에서 같은 걸러내기를 쓴다.
 */
function useNames(raw: (string | null)[]): ReadonlyMap<string, string> {
  const ids = [...new Set(raw.filter((id): id is string => id !== null))]
  ids.sort()
  const result = useQuery<Map<string, string>>({
    queryKey: ['users', 'names', ids],
    queryFn: async () => {
      const page = await usersApi.list({ ids, limit: 100 })
      return new Map(page.items.map((u) => [u.id, u.display_name]))
    },
    enabled: ids.length > 0,
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  })
  return result.data ?? NO_NAMES
}

/** `::chart` 가 그릴 막대 수. 서버(`directives.py`)와 같은 값이어야 한다. */
const MAX_BARS = 50
const DEFAULT_BARS = 10

/** 키가 UUID 로 오는 기준. 이름을 붙여 줘야 읽을 수 있다. */
const USER_GROUPS = ['assignee', 'reporter']

/**
 * `::chart{query=… group=… limit=…}` — 문서 안의 IQL 집계 (B9b).
 *
 * **세는 것은 리포트와 같은 자리다** (`POST /reports/count`, A29). 문서용으로
 * 따로 세면 같은 질의가 두 화면에서 다른 수를 말하는 날이 온다. 권한도 그쪽이
 * 거른다 — 볼 수 없는 이슈는 애초에 세어지지 않는다.
 *
 * 기준(`group`) 이름이 옳은지는 **서버가 판정한다.** 셀 수 있는 기준 목록은
 * 이슈 모듈 것이고, 여기서 베껴 두면 기준이 하나 늘 때 화면만 모른다. 틀린
 * 이름은 서버가 이유를 주고 그것을 그대로 그린다.
 */
function Chart({ attrs }: { attrs: Record<string, string> }) {
  const { t } = useTranslation(['markdown'])
  const iql = (attrs['query'] ?? '').trim()
  const group = (attrs['group'] ?? '').trim().toLowerCase()
  const bars = clampBars(attrs['limit'])

  const result = useQuery({
    queryKey: ['markdown', 'chart', iql, group],
    queryFn: () => reportsApi.count({ iql, group_by: group }),
    enabled: iql !== '' && group !== '',
    staleTime: 30_000,
  })

  const report = result.data
  const names = useNames(
    report !== undefined && USER_GROUPS.includes(report.group_by)
      ? report.buckets.map((bucket) => bucket.key)
      : [],
  )

  if (iql === '') return <DirectiveNote>{t('markdown:chart.needsQuery')}</DirectiveNote>
  if (group === '') return <DirectiveNote>{t('markdown:chart.needsGroup')}</DirectiveNote>
  if (result.isPending) return <DirectiveNote>{t('markdown:chart.loading')}</DirectiveNote>
  // 질의 문법도 기준 이름도 서버가 판정한다. 그 이유를 그대로 보여 준다.
  if (result.isError) return <DirectiveNote tone="error">{describeError(result.error)}</DirectiveNote>

  return (
    <div className="ieum-directive my-3">
      <CountBars
        report={result.data}
        bars={bars}
        label={(bucket) => {
          if (bucket.key === null) return t('markdown:chart.none')
          return USER_GROUPS.includes(result.data.group_by)
            ? (names.get(bucket.key) ?? bucket.key)
            : bucket.key
        }}
      />
      {/*
        **숫자에서 목록으로 갈 수 있어야 한다.** "40건" 을 보고 "어떤 40건
        인가" 를 물을 수 없으면 문서에 박힌 그림은 막다른 길이다.
      */}
      <Link
        to="/issues"
        search={{ iql }}
        className="text-xs text-accent hover:underline"
      >
        {t('markdown:chart.open')}
      </Link>
    </div>
  )
}

function clampBars(raw: string | undefined): number {
  const value = raw ? Number(raw) : NaN
  return Number.isInteger(value) && value >= 1 && value <= MAX_BARS ? value : DEFAULT_BARS
}

/**
 * `::excerpt{page="SPACE/경로"}` — 다른 문서의 앞부분.
 *
 * 권한은 서버가 거른다. 못 보는 문서는 404 로 오고 화면도 "없거나 볼 수 없다"
 * 라고만 말한다 — "권한이 없다" 는 그 문서가 있다는 뜻이라 그 자체가 정보다.
 */
function Excerpt({ path }: { path: string }) {
  const { t } = useTranslation(['markdown'])
  const trimmed = path.trim().replace(/^\/+/, '')
  const slash = trimmed.indexOf('/')
  const space = slash === -1 ? trimmed : trimmed.slice(0, slash)
  const rest = slash === -1 ? '' : trimmed.slice(slash + 1)

  const page = useQuery({
    queryKey: ['wiki', 'excerpt', space, rest],
    queryFn: () => wikiApi.pages.getByPath(space, rest),
    enabled: space !== '' && rest !== '',
    retry: false,
    staleTime: 30_000,
  })

  if (space === '' || rest === '') return <DirectiveNote>{t('markdown:excerpt.needsPage')}</DirectiveNote>
  if (page.isPending) return <DirectiveNote>{t('markdown:excerpt.loading')}</DirectiveNote>
  if (page.isError) return <DirectiveNote tone="error">{t('markdown:excerpt.missing')}</DirectiveNote>

  const summary = firstParagraph(page.data.body)
  return (
    <blockquote className="ieum-directive my-3 border-l-2 border-border pl-3">
      <Link
        to="/wiki/$spaceKey/$"
        params={{ spaceKey: space, _splat: rest }}
        className="text-sm font-medium text-accent"
      >
        {page.data.title}
      </Link>
      {summary ? (
        <MarkdownHtml html={renderMarkdown(summary)} className="text-sm text-muted" />
      ) : (
        <p className="text-sm text-muted">{t('markdown:excerpt.empty')}</p>
      )}
    </blockquote>
  )
}

function DirectiveNote({
  children,
  tone = 'muted',
}: {
  children: React.ReactNode
  tone?: 'muted' | 'error'
}) {
  return (
    <p className={tone === 'error' ? 'ieum-directive text-sm text-danger' : 'ieum-directive text-sm text-muted'}>
      {children}
    </p>
  )
}
