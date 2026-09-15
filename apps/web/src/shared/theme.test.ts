import { describe, expect, it } from 'vitest'

import { resolve } from './theme'

describe('테마 고르기', () => {
  it('시스템을 따르면 시스템이 정한다', () => {
    expect(resolve('system', true)).toBe('dark')
    expect(resolve('system', false)).toBe('light')
  })

  /**
   * **고른 것이 시스템을 이긴다.** 이게 뒤집히면, 어두운 OS 를 쓰면서
   * 앱만 밝게 보려는 사람이 고를 수가 없다 — 고르는 상자가 있는데 아무
   * 일도 안 일어나는 것이 제일 나쁘다.
   */
  it('직접 고르면 시스템 설정을 덮는다', () => {
    expect(resolve('light', true)).toBe('light')
    expect(resolve('dark', false)).toBe('dark')
  })
})
