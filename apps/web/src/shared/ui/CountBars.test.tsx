import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { CountReport } from '@ieum/api-client'

import { CountBars } from './CountBars'

// 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 안 깨진다.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

function report(over: Partial<CountReport> = {}): CountReport {
  return {
    group_by: 'status',
    buckets: [
      { key: '진행 중', count: 7 },
      { key: null, count: 3 },
    ],
    total: 10,
    multi_valued: false,
    truncated: false,
    supported: ['status'],
    ...over,
  }
}

const label = (bucket: { key: string | null }): string => bucket.key ?? '값 없음'

describe('CountBars', () => {
  it('칸마다 이름과 수를 글자로 적는다', () => {
    // 색·길이만으로 뜻을 전하면 화면 낭독기에는 아무것도 안 남는다
    // (ux-principles 5절).
    render(<CountBars report={report()} label={label} />)
    expect(screen.getByRole('rowheader', { name: '진행 중' })).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('값이 없는 칸도 이름을 받는다', () => {
    // 빈 이름으로 그리면 "담당자 없음 3건" 이 이름 없는 줄로 보인다.
    render(<CountBars report={report()} label={label} />)
    expect(screen.getByRole('rowheader', { name: '값 없음' })).toBeInTheDocument()
  })

  it('막대는 낭독기에서 숨는다', () => {
    // 막대는 눈으로 크기를 견주는 보조다. 읽히면 같은 값이 두 번 나온다.
    const { container } = render(<CountBars report={report()} label={label} />)
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBe(2)
  })

  it('합이 총계보다 큰 이유를 말한다', () => {
    // 안 말하면 사람은 숫자가 안 맞는 것을 버그로 보고 리포트를 안 믿는다.
    render(<CountBars report={report({ multi_valued: true })} label={label} />)
    expect(screen.getByText('reports:multiValued')).toBeInTheDocument()
  })

  it('칸 상한에서 잘렸으면 말한다', () => {
    render(<CountBars report={report({ truncated: true })} label={label} />)
    expect(screen.getByText('reports:truncated')).toBeInTheDocument()
  })

  it('안 그린 칸이 몇 개인지 말한다', () => {
    // 조용히 자르면 문서가 "이게 전부" 라고 거짓말을 한다.
    render(<CountBars report={report()} label={label} bars={1} />)
    expect(screen.getByRole('rowheader', { name: '진행 중' })).toBeInTheDocument()
    expect(screen.queryByRole('rowheader', { name: '값 없음' })).not.toBeInTheDocument()
    expect(screen.getByText('reports:moreBuckets')).toBeInTheDocument()
  })

  it('다 그렸으면 안 그린 칸을 말하지 않는다', () => {
    render(<CountBars report={report()} label={label} bars={2} />)
    expect(screen.queryByText('reports:moreBuckets')).not.toBeInTheDocument()
  })

  it('칸이 없으면 표를 그리지 않는다', () => {
    render(<CountBars report={report({ buckets: [], total: 0 })} label={label} />)
    expect(screen.getByText('reports:empty')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('칸이 없어도 총계는 적는다', () => {
    /**
     * 칸이 0개인데 총계가 0이 아닌 경우가 있다: 라벨로 묶으면 라벨이 없는
     * 이슈는 집계에 행을 하나도 안 만든다. 그때 "맞는 이슈가 없다" 만
     * 보여 주면 5건이 0건으로 보인다.
     */
    render(
      <CountBars
        report={report({ group_by: 'labels', buckets: [], total: 5, multi_valued: true })}
        label={label}
      />,
    )
    expect(screen.getByText('reports:total')).toBeInTheDocument()
    expect(screen.getByText('reports:empty')).toBeInTheDocument()
  })

  it('가장 큰 칸이 100% 다', () => {
    // 0 으로 나누거나 상대 크기를 뒤집으면 그림이 값과 다른 말을 한다.
    const { container } = render(<CountBars report={report()} label={label} />)
    const widths = [...container.querySelectorAll<HTMLElement>('[aria-hidden="true"]')].map(
      (bar) => bar.style.width,
    )
    expect(widths).toEqual(['100%', `${String((3 / 7) * 100)}%`])
  })
})
