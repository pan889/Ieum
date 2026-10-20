/**
 * 이슈 목록의 쪽 넘김·고른 줄이 **다른 질의로 새지 않는가.**
 *
 * 질의를 바꾸는 길은 화면 안의 필터만이 아니다 — 뒤로 가기, 사이드바의
 * "이슈", 저장한 필터 링크가 주소를 바로 바꾼다. 그 길로 오면 옛 질의의
 * 커서가 새 질의에 붙어 새 결과의 엉뚱한 쪽이 열렸고, 안 보이는 옛 줄이 고른
 * 채로 남아 일괄 편집에 실렸다. 여기서는 **주소만** 바꿔 그 길을 흉내 낸다.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { IssueSummary } from '@ieum/api-client'

import type { IssuesSearch } from './urlState'
import { IssuesScreen } from './IssuesScreen'

// `vi.mock` 의 팩토리는 끌어올려지므로 위에서 만든 변수를 못 본다.
const { route, search } = vi.hoisted(() => {
  const route: { search: IssuesSearch } = { search: {} }
  return { route, search: vi.fn() }
})

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => route.search,
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a href="/issues">{children}</a>,
}))
vi.mock('@/shared/api', () => ({ searchApi: { search, exportCsv: vi.fn() } }))
// 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 안 깨진다.
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('./hooks', () => ({ useUserNames: () => ({ data: new Map<string, string>() }) }))
vi.mock('./FilterBar', () => ({ FilterBar: () => null }))
vi.mock('./SavedFilters', () => ({ SavedFilters: () => null }))
// 일괄 편집 막대는 **몇 줄을 들고 있는가** 만 보여 준다.
vi.mock('./BulkBar', () => ({
  BulkBar: ({ selected }: { selected: string[] }) => <p>{`bulk:${selected.join(',')}`}</p>,
}))

function row(key: string): IssueSummary {
  return {
    id: `id-${key}`,
    key,
    key_seq: 1,
    project_id: 'p1',
    summary: `summary ${key}`,
    state_id: 's1',
    state_name: 'To do',
    state_category: 'todo',
    assignee_id: null,
    priority: 3,
    due_date: null,
    updated_at: '2026-01-01T00:00:00Z',
  }
}

/** 질의 A 는 두 쪽, 질의 B 는 한 쪽이다. */
function answer({ iql, cursor }: { iql: string; cursor?: string }) {
  if (iql === 'project = A') {
    return cursor === undefined
      ? { items: [row('A-1')], next_cursor: 'a-page-2', total: null }
      : { items: [row('A-2')], next_cursor: null, total: null }
  }
  return { items: [row('B-1')], next_cursor: null, total: null }
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = () => (
    <QueryClientProvider client={client}>
      <IssuesScreen />
    </QueryClientProvider>
  )
  const view = render(tree())
  return {
    /** 화면 밖에서 주소만 바꾼다(뒤로 가기·사이드바 링크). */
    navigateTo(next: IssuesSearch) {
      route.search = next
      view.rerender(tree())
    },
  }
}

beforeEach(() => {
  route.search = { iql: 'project = A' }
  search.mockReset()
  search.mockImplementation((body: { iql: string; cursor?: string }) =>
    Promise.resolve(answer(body)),
  )
})

describe('IssuesScreen / 질의가 밖에서 바뀔 때', () => {
  it('옛 질의의 커서로 새 질의를 묻지 않는다', async () => {
    const view = show()
    fireEvent.click(await screen.findByRole('button', { name: 'issues:list.loadMore' }))
    expect(await screen.findByText('summary A-2')).toBeInTheDocument()

    view.navigateTo({ iql: 'project = B' })

    expect(await screen.findByText('summary B-1')).toBeInTheDocument()
    // B 를 물은 것은 **커서 없이**여야 한다. A 의 2쪽 커서를 붙이면 서버는
    // 다른 목록의 자리를 받는다.
    const askedForB = search.mock.calls
      .map(([body]) => body as { iql: string; cursor?: string })
      .filter((body) => body.iql === 'project = B')
    expect(askedForB).toEqual([{ iql: 'project = B', limit: 50 }])
    // 처음 쪽이므로 "앞으로" 단추도 없다.
    expect(screen.queryByRole('button', { name: '←' })).toBeNull()
  })

  it('옛 질의에서 고른 줄을 새 질의로 들고 가지 않는다', async () => {
    const view = show()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'issues:bulk.select' }))
    expect(screen.getByText('bulk:id-A-1')).toBeInTheDocument()

    view.navigateTo({ iql: 'project = B' })

    await waitFor(() => { expect(screen.queryByText(/^bulk:/)).toBeNull() })
    expect(await screen.findByText('summary B-1')).toBeInTheDocument()
  })
})
