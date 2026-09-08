import { describe, expect, it } from 'vitest'

import { inBacklog, inSprint, quote } from './iql'

describe('quote', () => {
  it('평범한 이름은 그대로 감싼다', () => {
    expect(quote('Cycle 1')).toBe('"Cycle 1"')
  })

  it('따옴표를 막는다 — 안 그러면 질의가 깨지거나 다른 조건이 된다', () => {
    expect(quote('He said "go"')).toBe('"He said \\"go\\""')
  })

  it('**역슬래시를 먼저** 늘린다', () => {
    // 따옴표를 먼저 처리하면 그때 붙인 역슬래시까지 늘어나서 값이 달라진다.
    expect(quote('a\\b')).toBe('"a\\\\b"')
    expect(quote('a\\"b')).toBe('"a\\\\\\"b"')
  })
})

describe('문장', () => {
  it('스프린트 안', () => {
    expect(inSprint('ABC', 'Cycle 1')).toBe('project = ABC AND sprint = "Cycle 1"')
  })

  it('백로그는 IS EMPTY 다 — 이름으로 묻지 않는다', () => {
    expect(inBacklog('ABC')).toBe('project = ABC AND sprint IS EMPTY')
  })
})
