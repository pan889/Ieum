/**
 * 이슈 상세의 **편집 상태가 어느 이슈의 것인가.**
 *
 * 라우터는 `/issues/A` 에서 `/issues/B` 로 가도 화면을 다시 마운트하지 않는다.
 * 그래서 화면이 스스로 끊지 않으면 A 에서 쓰던 초안이 B 의 화면에 남고, 저장을
 * 누르면 그 글이 B 를 덮는다. 눈으로는 "B 를 고치는 중" 으로 보이므로 아무도
 * 못 알아챈다 — 여기서 잡는다.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Issue } from '@ieum/api-client'

import { IssueDetailScreen } from './IssueDetailScreen'

// `vi.mock` 의 팩토리는 끌어올려지므로 위에서 만든 변수를 못 본다.
const { route, api } = vi.hoisted(() => ({
  route: { issueKey: 'ENG-1' },
  api: {
    issuesApi: {
      getByKey: vi.fn(),
      change: vi.fn(),
      update: vi.fn(),
      transitions: vi.fn(),
      transition: vi.fn(),
      comments: vi.fn(),
      addComment: vi.fn(),
      history: vi.fn(),
      archive: vi.fn(),
    },
    deskApi: { agentTicket: vi.fn(), listCannedResponses: vi.fn() },
    sprintsApi: { list: vi.fn(), assign: vi.fn() },
  },
}))

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ issueKey: route.issueKey }),
  Link: ({ children }: { children: React.ReactNode }) => <a href="/issues">{children}</a>,
}))
vi.mock('@/shared/api', () => api)
// 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 안 깨진다.
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('./hooks', () => ({
  useFieldDefinitions: () => ({ data: [{ id: 'f1', key: 'severity', name: 'Severity' }] }),
  useUserNames: () => ({ data: new Map<string, string>() }),
  useUserSearch: () => ({ data: undefined }),
}))
// 이 시험이 보는 것은 **이 화면이 들고 있는 상태**다. 옆 칸들은 각자 자기
// 조회를 하므로 비워 둔다 — 남겨 두면 그 조회를 다 흉내 내야 한다.
vi.mock('./Approvals', () => ({ Approvals: () => null }))
vi.mock('./AppSlots', () => ({ AppSlots: () => null }))
vi.mock('./Assets', () => ({ Assets: () => null }))
vi.mock('./Attachments', () => ({ Attachments: () => null }))
vi.mock('./Development', () => ({ Development: () => null }))
vi.mock('./LinkedDocs', () => ({ LinkedDocs: () => null }))
vi.mock('./Relations', () => ({ Relations: () => null }))
vi.mock('./TimeTracking', () => ({ TimeTracking: () => null }))
vi.mock('@/features/notifications/WatchButton', () => ({ WatchButton: () => null }))
vi.mock('@/shared/markdown/RichText', () => ({
  RichText: ({ source }: { source: string }) => <div>{source}</div>,
}))
// 편집기는 글상자 하나로 갈음한다. 여기서 볼 것은 값이 어디서 오는가뿐이다.
vi.mock('@/shared/markdown/MarkdownEditor', () => ({
  MarkdownEditor: ({
    label,
    value,
    onChange,
  }: {
    label: string
    value: string
    onChange: (next: string) => void
  }) => (
    <textarea
      aria-label={label}
      value={value}
      onChange={(e) => { onChange(e.target.value) }}
    />
  ),
}))
vi.mock('./CustomField', () => ({
  CustomField: ({
    definition,
    value,
    onChange,
  }: {
    definition: { name: string }
    value: unknown
    onChange: (next: string) => void
  }) => (
    <input
      aria-label={definition.name}
      value={typeof value === 'string' ? value : ''}
      onChange={(e) => { onChange(e.target.value) }}
    />
  ),
}))

function issue(key: string, over: Partial<Issue> = {}): Issue {
  return {
    id: `id-${key}`,
    key,
    project_id: 'p1',
    summary: `summary of ${key}`,
    description: `description of ${key}`,
    type_id: 't1',
    type_name: 'Task',
    state_id: 's1',
    state_name: 'To do',
    state_category: 'todo',
    reporter_id: 'u1',
    assignee_id: null,
    priority: 3,
    parent_id: null,
    start_date: null,
    due_date: null,
    estimate_minutes: null,
    progress: 0,
    sprint_id: null,
    resolved_at: null,
    archived_at: null,
    version: 1,
    labels: [],
    custom_fields: { severity: `stored ${key}` },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

const A = issue('ENG-1')
const B = issue('ENG-2')

/**
 * 두 이슈를 **이미 캐시에 둔 채로** 연다. 한 번 열어 본 이슈로 돌아가는
 * 경우다 — 그때는 로딩 화면이 안 끼어서 상태가 저절로 지워지지도 않는다.
 */
function show() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['issues', 'detail', A.key], A)
  client.setQueryData(['issues', 'detail', B.key], B)
  const view = render(
    <QueryClientProvider client={client}>
      <IssueDetailScreen />
    </QueryClientProvider>,
  )
  return {
    goTo(key: string) {
      route.issueKey = key
      view.rerender(
        <QueryClientProvider client={client}>
          <IssueDetailScreen />
        </QueryClientProvider>,
      )
    },
  }
}

const summaryBox = () => screen.getByLabelText<HTMLInputElement>('issues:create.summary')

beforeEach(() => {
  route.issueKey = A.key
  api.issuesApi.getByKey.mockImplementation((key: string) =>
    Promise.resolve(key === B.key ? B : A),
  )
  api.issuesApi.transitions.mockResolvedValue([])
  api.issuesApi.comments.mockResolvedValue([])
  api.issuesApi.history.mockResolvedValue([])
  api.deskApi.agentTicket.mockResolvedValue({ ticket: null })
  api.deskApi.listCannedResponses.mockResolvedValue([])
  api.sprintsApi.list.mockResolvedValue([])
})

describe('IssueDetailScreen / 다른 이슈로 넘어갈 때', () => {
  it('고치던 요약·설명을 다음 이슈로 들고 가지 않는다', () => {
    const view = show()
    fireEvent.click(screen.getByRole('button', { name: 'issues:detail.edit' }))
    fireEvent.change(summaryBox(), { target: { value: 'A 에 쓰려던 요약' } })

    view.goTo(B.key)

    // 편집 칸이 닫히고 B 의 본문이 보여야 한다. 칸이 남아 있으면 저장 한
    // 번에 A 의 초안이 B 를 덮는다.
    expect(screen.queryByLabelText('issues:create.summary')).toBeNull()
    expect(screen.getByText(`description of ${B.key}`)).toBeInTheDocument()

    // 다시 열면 B 의 원본으로 연다.
    fireEvent.click(screen.getByRole('button', { name: 'issues:detail.edit' }))
    expect(summaryBox().value).toBe(B.summary)
  })

  it('고치던 커스텀 필드를 다음 이슈에 싣지 않는다', () => {
    const view = show()
    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'A 용 값' } })
    // 건드린 것이 있으면 저장 단추가 선다 — 초안이 살아 있다는 뜻이다.
    expect(screen.getByRole('button', { name: 'issues:detail.saveFields' })).toBeInTheDocument()

    view.goTo(B.key)

    expect(screen.getByLabelText<HTMLInputElement>('Severity').value).toBe(`stored ${B.key}`)
    expect(screen.queryByRole('button', { name: 'issues:detail.saveFields' })).toBeNull()
  })

  it('쓰던 코멘트를 다음 이슈에 남기지 않는다', () => {
    const view = show()
    fireEvent.change(screen.getByLabelText('issues:comment.title'), {
      target: { value: 'A 에 달려던 코멘트' },
    })

    view.goTo(B.key)

    expect(screen.getByLabelText<HTMLTextAreaElement>('issues:comment.title').value).toBe('')
  })
})

describe('IssueDetailScreen / `e` 단축키', () => {
  it('편집 중에 누르면 저장 안 한 초안을 지우지 않는다', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'issues:detail.edit' }))
    fireEvent.change(summaryBox(), { target: { value: '아직 저장 안 한 요약' } })

    // 입력 칸 밖에 초점이 있을 때다. 칸 안이면 단축키는 애초에 죽는다.
    act(() => {
      fireEvent.keyDown(document.body, { key: 'e' })
    })

    expect(summaryBox().value).toBe('아직 저장 안 한 요약')
  })

  it('편집 중이 아니면 편집을 연다', () => {
    show()
    act(() => {
      fireEvent.keyDown(document.body, { key: 'e' })
    })
    expect(summaryBox().value).toBe(A.summary)
  })
})
