/**
 * 통합 검색 결과 (ADR-0005).
 *
 * 이슈와 문서가 한 목록에 섞여 나온다. 권한은 서버가 질의에 얹어 걸렀으므로
 * 여기서 다시 거르지 않는다 — 두 곳에서 거르면 어느 쪽이 진실인지 알 수 없다.
 *
 * 검색어를 URL 이 소유한다. 링크 하나로 같은 결과가 나와야 한다.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { SearchHit, SearchKind } from '@ieum/api-client'
import { formatDateTime } from '@/features/issues/format'
import { searchApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

import { splitByKeywords } from './highlight'

const PAGE_SIZE = 20
const KINDS: SearchKind[] = ['issue', 'page']

export function SearchScreen() {
  const search = useSearch({ from: '/search' })
  // 주소가 바뀌면 입력칸도 따라가야 한다(주소창에 직접 치거나, 뒤로 가거나).
  // key 로 다시 마운트시킨다 — effect 에서 setState 하면 한 번 더 그린다.
  return <SearchView key={search.q} />
}

function SearchView() {
  const { t } = useTranslation(['search', 'common'])
  const navigate = useNavigate()
  const search = useSearch({ from: '/search' })
  const [draft, setDraft] = useState(search.q)

  const kinds = search.kind ? [search.kind] : KINDS
  const results = useQuery({
    queryKey: ['search', 'everything', search.q, search.kind, search.offset],
    queryFn: () =>
      searchApi.everything({
        q: search.q,
        kind: kinds,
        limit: PAGE_SIZE,
        offset: search.offset,
      }),
    enabled: search.q.trim() !== '',
    // 다음 쪽을 받는 동안 목록이 비지 않게.
    placeholderData: keepPreviousData,
  })

  const go = (next: { q?: string; kind?: SearchKind | undefined }) => {
    void navigate({ to: '/search', search: { ...search, offset: 0, ...next } })
  }
  const page = (offset: number) => {
    void navigate({ to: '/search', search: { ...search, offset } })
  }

  const total = results.data?.total ?? 0
  const shown = search.offset + (results.data?.items.length ?? 0)

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-4">
      <form
        className="flex items-end gap-2"
        onSubmit={(event) => { event.preventDefault(); go({ q: draft }) }}
      >
        <Field
          label={t('search:query')}
          className="flex-1"
          autoFocus
          value={draft}
          onChange={(e) => { setDraft(e.target.value) }}
        />
        <Button type="submit">{t('search:run')}</Button>
      </form>

      <div className="flex flex-wrap items-baseline gap-2 text-sm">
        <KindTab active={search.kind === undefined} onClick={() => { go({ kind: undefined }) }}>
          {t('search:kind.all')}
        </KindTab>
        {KINDS.map((kind) => (
          <KindTab key={kind} active={search.kind === kind} onClick={() => { go({ kind }) }}>
            {t(`search:kind.${kind}`)}
          </KindTab>
        ))}
        {results.data ? (
          <span className="ml-auto text-xs text-muted">{t('search:count', { count: total })}</span>
        ) : null}
      </div>

      {results.isError ? <Alert>{describeError(results.error)}</Alert> : null}
      {search.q.trim() === '' ? (
        <p className="text-sm text-muted">{t('search:empty')}</p>
      ) : !results.data ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : results.data.items.length === 0 ? (
        <p className="text-sm text-muted">{t('search:none', { query: search.q })}</p>
      ) : (
        /* 목록에 이름을 붙인다. 사이드바 메뉴도 목록이라 이름 없이는 구분이 안 된다. */
        <ul aria-label={t('search:results')} className="flex flex-col gap-2">
          {results.data.items.map((hit) => (
            <li key={`${hit.kind}:${hit.entity_id}`}>
              <Hit hit={hit} keywords={results.data.keywords} />
            </li>
          ))}
        </ul>
      )}

      {total > PAGE_SIZE ? (
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            className="text-xs"
            disabled={search.offset === 0}
            onClick={() => { page(Math.max(0, search.offset - PAGE_SIZE)) }}
          >
            {t('common:action.previous')}
          </Button>
          <span className="text-xs text-muted">{t('search:range', { shown, total })}</span>
          <Button
            variant="ghost"
            className="text-xs"
            disabled={shown >= total}
            onClick={() => { page(search.offset + PAGE_SIZE) }}
          >
            {t('common:action.next')}
          </Button>
        </div>
      ) : null}
    </section>
  )
}

function KindTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={
        active
          ? 'rounded border border-accent bg-accent px-2 py-0.5 text-xs text-accent-fg'
          : 'rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-fg'
      }
      onClick={onClick}
    >
      {children}
    </button>
  )
}

function Hit({ hit, keywords }: { hit: SearchHit; keywords: string[] }) {
  const { t } = useTranslation(['search'])
  return (
    <Card className="flex flex-col gap-1">
      <div className="flex items-baseline gap-2">
        <Badge tone={hit.kind === 'issue' ? 'in_progress' : 'neutral'}>
          {t(`search:kind.${hit.kind}`)}
        </Badge>
        <HitLink hit={hit} />
        <span className="ml-auto shrink-0 text-xs text-muted">
          {formatDateTime(hit.updated_at)}
        </span>
      </div>
      {hit.snippet ? (
        <p className="text-sm text-muted">
          <Highlighted text={hit.snippet} keywords={keywords} />
        </p>
      ) : null}
    </Card>
  )
}

function HitLink({ hit }: { hit: SearchHit }) {
  if (hit.kind === 'issue') {
    return (
      <Link
        to="/issues/$issueKey"
        params={{ issueKey: hit.ref }}
        className="min-w-0 truncate font-medium text-accent hover:underline"
      >
        <span className="mr-2 font-mono text-xs">{hit.ref}</span>
        {hit.title}
      </Link>
    )
  }
  // 문서 ref 는 `SPACE/path/to/page` 다. 앞 한 조각이 스페이스 키.
  const [spaceKey = '', ...rest] = hit.ref.split('/')
  return (
    <Link
      to="/wiki/$spaceKey/$"
      params={{ spaceKey, _splat: rest.join('/') }}
      className="min-w-0 truncate font-medium text-accent hover:underline"
    >
      <span className="mr-2 font-mono text-xs">{spaceKey}</span>
      {hit.title}
    </Link>
  )
}

/**
 * 검색어를 굵게. 서버가 보낸 HTML 을 그리지 않는다 — 본문에 HTML 을
 * 흘려보내는 통로를 하나 더 만들지 않기 위해서다.
 */
function Highlighted({ text, keywords }: { text: string; keywords: string[] }) {
  return (
    <>
      {splitByKeywords(text, keywords).map((part, index) =>
        part.hit ? (
          <mark key={index} className="ieum-quote">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  )
}
