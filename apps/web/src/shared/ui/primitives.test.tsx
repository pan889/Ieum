import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Button } from './primitives'

describe('Button', () => {
  it('폼 안에서도 제출하지 않는다', () => {
    // 폼 안의 button 은 기본이 submit 이다. 그래서 보조 버튼이 자기 일도
    // 하고 저장까지 해 버렸다 — 초안 "버리기" 가 버리면서 저장했다.
    const submitted = vi.fn((e: React.SyntheticEvent) => { e.preventDefault() })
    const clicked = vi.fn()
    render(
      <form onSubmit={submitted}>
        <Button onClick={clicked}>버리기</Button>
      </form>,
    )
    screen.getByRole('button', { name: '버리기' }).click()

    expect(clicked).toHaveBeenCalledOnce()
    expect(submitted).not.toHaveBeenCalled()
  })

  it('제출하려면 그렇다고 말한다', () => {
    const submitted = vi.fn((e: React.SyntheticEvent) => { e.preventDefault() })
    render(
      <form onSubmit={submitted}>
        <Button type="submit">저장</Button>
      </form>,
    )
    screen.getByRole('button', { name: '저장' }).click()

    expect(submitted).toHaveBeenCalledOnce()
  })

  it('로딩 중에는 눌리지 않는다', () => {
    const clicked = vi.fn()
    render(
      <Button loading onClick={clicked}>
        저장
      </Button>,
    )
    const button = screen.getByRole('button', { name: '저장' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
  })
})
