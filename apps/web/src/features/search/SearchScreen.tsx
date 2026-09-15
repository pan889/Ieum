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
import { Alert, Badge, Button, Chip, EmptyState, Field, PageHeader } from '@/shared/ui/primitives'

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
      {/* 화면에 이름이 없었다. 작은 라벨 하나와 상자가 위에 떠 있으면
          여기가 어디인지 말해 주는 것이 아무것도 없다. */}
      <PageHeader title={t('search:query')} />

      <form
        className="flex items-center gap-2"
        onSubmit={(event) => { event.preventDefault(); go({ q: draft }) }}
      >
        {/* `Field` 의 `className` 은 **입력에** 붙는다. 감싸개에 `flex-1` 을
            주려면 감싸개를 여기서 만들어야 한다 — 전에는 상자가 안 늘어나
            검색창이 화면 폭의 1/3 이었다. */}
        <div className="flex-1">
          <Field
            label={t('search:query')}
            labelHidden
            className="w-full"
            autoFocus
            value={draft}
            onChange={(e) => { setDraft(e.target.value) }}
          />
        </div>
        <Button type="submit">{t('search:run')}</Button>
      </form>

      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        {/* 갈래는 필터 막대의 칩과 **같은 것**이다. 여기서만 손으로 그린
            작은 알약을 쓰면, 같은 조작이 화면마다 다르게 생긴다. */}
        <Chip pressed={search.kind === undefined} onClick={() => { go({ kind: undefined }) }}>
          {t('search:kind.all')}
        </Chip>
        {KINDS.map((kind) => (
          <Chip key={kind} pressed={search.kind === kind} onClick={() => { go({ kind }) }}>
            {t(`search:kind.${kind}`)}
          </Chip>
        ))}
        {results.data ? (
          <span className="ml-auto text-xs tabular-nums text-subtle">
            {t('search:count', { count: total })}
          </span>
        ) : null}
      </div>

      {results.isError ? <Alert>{describeError(results.error)}</Alert> : null}
      {search.q.trim() === '' ? (
        <EmptyState title={t('search:empty')} />
      ) : !results.data ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : results.data.items.length === 0 ? (
        <EmptyState title={t('search:none', { query: search.q })} />
      ) : (
        /* 목록에 이름을 붙인다. 사이드바 메뉴도 목록이라 이름 없이는 구분이
           안 된다. 결과 하나에 카드 하나를 주지 않는다 — 스무 개를 훑는
           화면에서 한 줄에 88픽셀은 세 번 스크롤이다. */
        <ul
          aria-label={t('search:results')}
          className="divide-y divide-border overflow-hidden rounded-card border border-border bg-surface shadow-raised"
        >
          {results.data.items.map((hit) => (
            <li
              key={`${hit.kind}:${hit.entity_id}`}
              className="px-3 py-2 hover:bg-surface-raised"
            >
              <Hit hit={hit} keywords={results.data.keywords} />
            </li>
          ))}
        </ul>
      )}

      {total > PAGE_SIZE ? (
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={search.offset === 0}
            onClick={() => { page(Math.max(0, search.offset - PAGE_SIZE)) }}
          >
            {t('common:action.previous')}
          </Button>
          <span className="text-xs tabular-nums text-subtle">
            {t('search:range', { shown, total })}
          </span>
          <Button
            variant="secondary"
            size="sm"
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

function Hit({ hit, keywords }: { hit: SearchHit; keywords: string[] }) {
  const { t } = useTranslation(['search'])
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline gap-2">
        <Badge tone={hit.kind === 'issue' ? 'in_progress' : 'neutral'}>
          {t(`search:kind.${hit.kind}`)}
        </Badge>
        <HitLink hit={hit} />
        <span className="ml-auto shrink-0 text-xs tabular-nums text-subtle">
          {formatDateTime(hit.updated_at)}
        </span>
      </div>
      {hit.snippet ? (
        <p className="line-clamp-2 text-sm text-muted">
          <Highlighted text={hit.snippet} keywords={keywords} />
        </p>
      ) : null}
    </div>
  )
}

function HitLink({ hit }: { hit: SearchHit }) {
  if (hit.kind === 'issue') {
    return (
      <Link
        to="/issues/$issueKey"
        params={{ issueKey: hit.ref }}
        className="min-w-0 truncate text-sm font-medium text-accent hover:underline"
      >
        <span className="mr-2 font-mono text-xs text-subtle">{hit.ref}</span>
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
      className="min-w-0 truncate text-sm font-medium text-accent hover:underline"
    >
      <span className="mr-2 font-mono text-xs text-subtle">{spaceKey}</span>
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
