/**
 * 참·거짓 커스텀 필드의 접근성 이름.
 *
 * 설명이 이름에 섞이면 `getByLabel(/regression/i)` 이 못 찾는다 —
 * `e2e/customFields.spec.ts` 가 그 셀렉터로 이 칸을 켠다. 여기 이름은
 * 관리자가 적은 값이라 화면 문구를 고정해 두는 것으로는 막을 수 없다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { FieldDefinition } from '@ieum/api-client'

import { CustomField } from './CustomField'

const definition = {
  id: 'f1',
  key: 'regression',
  name: 'Regression',
  kind: 'bool',
  is_required: false,
  description: 'Set when the bug is a regression.',
  config: {},
} as unknown as FieldDefinition

describe('CustomField / bool', () => {
  it('설명은 이름이 아니라 설명으로 붙는다', () => {
    render(
      <CustomField definition={definition} value={true} onChange={() => {}} projectId={null} />,
    )

    const box = screen.getByRole('checkbox', { name: 'Regression' })
    expect(box).toHaveAccessibleName('Regression')
    expect(box).toHaveAccessibleDescription('Set when the bug is a regression.')
    // Playwright 의 getByLabel(/regression/i) 과 같은 자리를 짚는다.
    expect(screen.getByLabelText(/regression/i)).toBe(box)
  })
})
