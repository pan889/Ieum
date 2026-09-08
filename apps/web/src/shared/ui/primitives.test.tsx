import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Button, Checkbox } from './primitives'

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

describe('Checkbox', () => {
  it('접근성 이름은 라벨뿐이다 — 힌트를 이어 붙이지 않는다', () => {
    // 손으로 조립하면 라벨과 힌트가 한 `<label>` 에 들어가고, 그러면 이름이
    // "Allow IdP-initiated Some clients start login from…" 처럼 두 문장을
    // 이어 붙인 것이 된다. 이름으로 찾는 모든 것이 — 화면 낭독기도, 브라우저
    // 시험의 getByRole('checkbox', { name }) 도 — 그 칸을 못 찾는다.
    render(
      <Checkbox
        label="Allow IdP-initiated"
        hint="Some clients start login from the IdP."
        checked={false}
        onChange={() => {}}
      />,
    )

    const box = screen.getByRole('checkbox', { name: 'Allow IdP-initiated' })
    expect(box).toHaveAccessibleName('Allow IdP-initiated')
    // 힌트는 사라지지 않는다. 이름이 아니라 설명으로 붙는다.
    expect(box).toHaveAccessibleDescription('Some clients start login from the IdP.')
  })

  it('힌트가 없으면 설명도 달지 않는다', () => {
    render(<Checkbox label="Encrypt assertions" checked={false} onChange={() => {}} />)

    // 가리킬 것이 없는 `aria-describedby` 는 남겨 두지 않는다.
    expect(screen.getByRole('checkbox', { name: 'Encrypt assertions' })).not.toHaveAttribute(
      'aria-describedby',
    )
  })

  it('라벨을 눌러도 체크된다', () => {
    // `htmlFor` 로 이어져 있어야 라벨이 누를 수 있는 자리가 된다. 안 이어져
    // 있으면 이름은 맞아도 과녁이 체크박스 한 칸으로 줄어든다.
    const changed = vi.fn()
    render(<Checkbox label="Share" checked={false} onChange={changed} />)

    screen.getByText('Share').click()

    expect(changed).toHaveBeenCalledOnce()
  })
})
