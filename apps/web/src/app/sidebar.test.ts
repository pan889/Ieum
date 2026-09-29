import { afterEach, describe, expect, it, vi } from 'vitest'

import { readSidebarCollapsed, writeSidebarCollapsed } from './sidebar'

describe('사이드바 접힘 상태', () => {
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('처음에는 펼쳐 두고 선택을 기억한다', () => {
    expect(readSidebarCollapsed()).toBe(false)
    writeSidebarCollapsed(true)
    expect(readSidebarCollapsed()).toBe(true)
    writeSidebarCollapsed(false)
    expect(readSidebarCollapsed()).toBe(false)
  })

  it('저장소를 쓸 수 없어도 화면은 동작한다', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })

    expect(readSidebarCollapsed()).toBe(false)
    expect(() => { writeSidebarCollapsed(true) }).not.toThrow()
  })
})
