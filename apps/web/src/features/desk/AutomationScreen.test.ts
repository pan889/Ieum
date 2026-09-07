/**
 * 자동화 폼이 서버보다 먼저 거절하는 것들 (feature-map C9).
 *
 * **자동화의 실패는 조용하다** — 규칙이 안 돈 것과 조건이 안 맞은 것이 화면에
 * 똑같이 "아무 일도 안 일어남" 으로 보인다. 그래서 저장 전에 막는 것이 다른
 * 화면보다 무겁다.
 *
 * 여기서 붙잡는 것:
 *
 * - **조치가 지목을 안 한 규칙은 저장을 막는다.** 서버도 거절하지만, 거절당한
 *   뒤 조치 다섯 줄 중 어디가 빈지 찾게 두지 않는다.
 * - **항목을 바꾸면 값도 바뀐다.** `priority >= 4` 에서 항목만 `is_internal`
 *   로 바꾸면 `is_internal >= 4` 가 남는다.
 * - **종류에 맞는 값만 통과한다.** `is_internal` 에 글자 `"true"` 를 두면
 *   서버는 참·거짓과 글자를 같다고 보지 않는다.
 */

import { describe, expect, it } from 'vitest'

import type { AutomationAction, AutomationCondition, AutomationTrigger } from '@ieum/api-client'

import { freshCondition, ruleReady } from './AutomationScreen'

const PRIORITY_ACTION: AutomationAction[] = [{ kind: 'set_priority', priority: 5 }]

function form(overrides: {
  name?: string
  conditions?: AutomationCondition[]
  actions?: AutomationAction[]
}) {
  return {
    name: '이름',
    trigger: 'desk.ticket.submitted' as AutomationTrigger,
    conditions: [],
    actions: PRIORITY_ACTION,
    ...overrides,
  }
}

describe('ruleReady', () => {
  it('이름과 조치가 있으면 저장할 수 있다', () => {
    expect(ruleReady(form({}))).toBe(true)
  })

  it('이름이 공백뿐이면 막는다', () => {
    expect(ruleReady(form({ name: '   ' }))).toBe(false)
  })

  it('조치가 없는 규칙은 막는다', () => {
    // 조건만 맞춰 보고 아무 것도 안 하는 규칙이다.
    expect(ruleReady(form({ actions: [] }))).toBe(false)
  })

  it('사람을 안 고른 배정은 막는다', () => {
    expect(ruleReady(form({ actions: [{ kind: 'assign' }] }))).toBe(false)
    expect(
      ruleReady(form({ actions: [{ kind: 'assign', user_id: 'u-1' }] })),
    ).toBe(true)
  })

  it('문구를 안 고른 회신은 막는다', () => {
    expect(ruleReady(form({ actions: [{ kind: 'reply_with_canned' }] }))).toBe(false)
    expect(ruleReady(form({ actions: [{ kind: 'add_note' }] }))).toBe(false)
  })

  it('고르는 항목에서 아무 것도 안 고른 조건은 막는다', () => {
    // 서버가 "UUID 가 아니다" 로 거절한다. 그전에 저장 단추가 꺼져 있어야
    // 무엇이 빈지 화면에 남는다.
    expect(
      ruleReady(form({ conditions: [{ field: 'request_type_id', op: 'eq', value: '' }] })),
    ).toBe(false)
  })

  it('여러 개 고르기에서 하나도 안 고르면 막는다', () => {
    // 빈 목록은 아무 것도 안 맞는 조건이라 규칙 전체가 조용히 죽는다.
    expect(ruleReady(form({ conditions: [{ field: 'channel', op: 'in', value: [] }] }))).toBe(
      false,
    )
    expect(
      ruleReady(form({ conditions: [{ field: 'channel', op: 'in', value: ['portal'] }] })),
    ).toBe(true)
  })

  it('참·거짓 항목에 글자가 들어 있으면 막는다', () => {
    // `true` 와 `"true"` 는 서버에서 다르다 — 저장되면 한 번도 안 걸린다.
    expect(
      ruleReady(form({ conditions: [{ field: 'is_internal', op: 'eq', value: 'true' }] })),
    ).toBe(false)
    expect(
      ruleReady(form({ conditions: [{ field: 'is_internal', op: 'eq', value: true }] })),
    ).toBe(true)
  })

  it('숫자 항목에 글자가 들어 있으면 막는다', () => {
    expect(ruleReady(form({ conditions: [{ field: 'priority', op: 'eq', value: '4' }] }))).toBe(
      false,
    )
    expect(ruleReady(form({ conditions: [{ field: 'priority', op: 'gte', value: 4 }] }))).toBe(
      true,
    )
  })

  it('거짓도 고른 값이다', () => {
    // `!value` 로 비어 있는지 보면 "내부 노트가 아님" 이 안 골라진 것이 된다.
    expect(
      ruleReady(form({ conditions: [{ field: 'is_internal', op: 'eq', value: false }] })),
    ).toBe(true)
  })
})

describe('freshCondition', () => {
  it('항목을 바꾸면 그 종류에 맞는 비교와 값으로 시작한다', () => {
    // `priority >= 4` 에서 항목만 바꾸면 `is_internal >= 4` 가 남는다.
    expect(freshCondition('is_internal')).toEqual({
      field: 'is_internal',
      op: 'eq',
      value: true,
    })
    expect(freshCondition('channel')).toEqual({ field: 'channel', op: 'eq', value: 'portal' })
    expect(freshCondition('state_category')).toEqual({
      field: 'state_category',
      op: 'eq',
      value: 'todo',
    })
    expect(freshCondition('priority')).toEqual({ field: 'priority', op: 'eq', value: 3 })
  })

  it('고르는 항목은 비워 둔다', () => {
    // 첫째를 미리 고르면, 값을 안 건드린 규칙이 우연히 첫째를 겨냥한 채
    // 저장된다. 비어 있으면 저장이 막힌다.
    const row = freshCondition('request_type_id')
    expect(row.value).toBe('')
    expect(ruleReady(form({ conditions: [row] }))).toBe(false)
  })

  it('만든 조건은 언제나 저장할 수 있는 모양이다', () => {
    for (const field of ['priority', 'channel', 'state_category', 'summary'] as const) {
      const row = freshCondition(field)
      // `summary` 만 빈 글자로 시작한다 — 적으라고 두는 자리다.
      expect(ruleReady(form({ conditions: [row] }))).toBe(field !== 'summary')
    }
  })
})
