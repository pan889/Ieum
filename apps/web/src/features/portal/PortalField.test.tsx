/**
 * 포털 폼의 참·거짓 칸.
 *
 * 라벨과 도움말이 한 `<label>` 안에 있었다. 이름이 두 문장을 이어 붙인 것이
 * 되면 이름으로 찾는 모든 것이 이 칸을 놓친다. 둘 다 고객이 보는 화면의
 * 관리자 입력값이라 문구로 고정할 수 없다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PortalFormField } from '@ieum/api-client'

import { PortalField } from './PortalField'

const field = {
  key: 'urgent',
  label: 'This is urgent',
  kind: 'bool',
  required: false,
  help: 'We answer urgent requests first.',
  config: {},
} as unknown as PortalFormField

describe('PortalField / bool', () => {
  it('도움말은 이름이 아니라 설명으로 붙는다', () => {
    render(<PortalField field={field} value={true} onChange={() => {}} />)

    const box = screen.getByRole('checkbox', { name: 'This is urgent' })
    expect(box).toHaveAccessibleName('This is urgent')
    expect(box).toHaveAccessibleDescription('We answer urgent requests first.')
  })

  it('필수 항목이어도 체크박스에 required 를 걸지 않는다', () => {
    // 참·거짓 항목에서 서버와 화면은 **꺼짐도 답**으로 친다. 입력 요소에
    // `required` 를 걸면 브라우저가 "켜야 보낼 수 있다" 로 해석해, 끄고
    // 보내려는 사람이 서버에 닿기도 전에 막힌다.
    render(
      <PortalField field={{ ...field, required: true }} value={false} onChange={() => {}} />,
    )

    const box = screen.getByRole('checkbox', { name: 'This is urgent' })
    expect(box).not.toBeRequired()
    // `*` 도 붙이지 않는다 — 켜라는 뜻으로 읽힌다.
    expect(box).toHaveAccessibleName('This is urgent')
  })
})
