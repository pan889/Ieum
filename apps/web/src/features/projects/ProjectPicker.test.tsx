import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Project } from '@ieum/api-client'

import { ProjectPicker } from './ProjectPicker'

// `vi.mock` 의 팩토리는 끌어올려지므로 위에서 만든 변수를 못 본다.
const { list } = vi.hoisted(() => ({ list: vi.fn() }))

vi.mock('@/shared/api', () => ({ projectsApi: { list } }))
// 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 이 시험은 안
// 깨져야 한다 — 깨지면 사람이 시험을 지운다.
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

function project(key: string): Project {
  return {
    id: key,
    key,
    name: key,
    description: null,
    parent_id: null,
    lead_id: null,
    is_public: true,
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function found(items: Project[]) {
  list.mockResolvedValue({ items, next_cursor: null, total: null })
}

function show(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

/** 검색을 펼친다. 피커는 접힌 채로 시작하므로 입력창이 아직 없다. */
function open(name: string) {
  fireEvent.click(screen.getByRole('button', { name }))
}

describe('ProjectPicker', () => {
  it('후보가 상한만큼 오면 잘렸다고 말한다', async () => {
    // 조용히 자르는 것이 세 번 문제가 됐다. 사람은 "이게 전부" 라고 읽고,
    // 자기 프로젝트가 왜 없는지 알 방법이 없다(ux-principles 4절).
    found(Array.from({ length: 20 }, (_, i) => project(`P${String(i)}`)))
    show(<ProjectPicker label="Project" chosen={null} onPick={vi.fn()} />)
    open('common:action.choose')

    expect(await screen.findByText('projects:pick.more')).toBeInTheDocument()
  })

  it('다 보여 줬으면 잘렸다고 하지 않는다', async () => {
    found([project('ENG')])
    show(<ProjectPicker label="Project" chosen={null} onPick={vi.fn()} />)
    open('common:action.choose')

    expect(await screen.findByRole('button', { name: 'ENG · ENG' })).toBeInTheDocument()
    expect(screen.queryByText('projects:pick.more')).not.toBeInTheDocument()
  })

  it('고르면 접히고 고른 것이 남는다', async () => {
    const onPick = vi.fn()
    found([project('ENG')])
    show(<ProjectPicker label="Project" chosen={null} onPick={onPick} />)
    open('common:action.choose')
    fireEvent.click(await screen.findByRole('button', { name: 'ENG · ENG' }))

    expect(onPick).toHaveBeenCalledWith(project('ENG'))
    // 검색 상자만 남으면 무엇이 골라져 있는지 사라진다.
    expect(screen.queryByRole('textbox', { name: 'Project' })).not.toBeInTheDocument()
  })

  it('안 골랐을 때 뭐라고 부를지는 부르는 쪽이 정한다', () => {
    // 목록 필터에서 안 고른 것은 빠뜨린 것이 아니라 "전체" 라는 뜻이다.
    show(<ProjectPicker label="Project" chosen={null} onPick={vi.fn()} emptyLabel="전체" />)

    expect(screen.getByText('전체')).toBeInTheDocument()
    expect(screen.queryByText('projects:pick.none')).not.toBeInTheDocument()
  })

  it('이름을 아직 못 읽었으면 키라도 보여 준다', () => {
    // 필터는 URL 에서 키만 받는다. 그 사이에 "전체" 라고 그리면 목록은
    // 걸러져 있는데 화면만 아니라고 말하는 셈이다.
    show(
      <ProjectPicker
        label="Project"
        chosen={null}
        onPick={vi.fn()}
        emptyLabel="전체"
        unresolvedLabel="ENG"
      />,
    )

    expect(screen.getByText('ENG')).toBeInTheDocument()
    expect(screen.queryByText('전체')).not.toBeInTheDocument()
  })

  it('되돌리는 버튼은 **되돌아갈 곳**의 이름으로 부른다', async () => {
    // 지금 고른 것의 키로 부르면("ENG") 무엇을 하는 버튼인지 정반대로
    // 읽힌다. 되돌아가는 곳은 "전체" 다.
    const onClear = vi.fn()
    found([project('ENG')])
    show(
      <ProjectPicker
        label="Project"
        chosen={project('OPS')}
        onPick={vi.fn()}
        emptyLabel="전체"
        unresolvedLabel="OPS"
        onClear={onClear}
      />,
    )
    open('projects:pick.change')
    fireEvent.click(await screen.findByRole('button', { name: '전체' }))

    expect(onClear).toHaveBeenCalledOnce()
  })

  it('되돌리는 길을 안 준 곳에는 그 버튼이 없다', async () => {
    found([project('ENG')])
    show(<ProjectPicker label="Project" chosen={null} onPick={vi.fn()} emptyLabel="전체" />)
    open('common:action.choose')

    await screen.findByRole('button', { name: 'ENG · ENG' })
    expect(screen.queryByRole('button', { name: '전체' })).not.toBeInTheDocument()
  })
})
