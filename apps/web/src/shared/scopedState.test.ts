/**
 * 질의에 딸린 상태가 **다른 질의로 새지 않는가.**
 *
 * 이슈 목록의 쪽 넘김 커서가 이것을 쓴다. 질의를 바꾸는 길이 화면 안의 단추만
 * 이 아니라서(뒤로 가기·사이드바·저장한 필터), 그 길로 오면 옛 질의의 커서가
 * 새 질의에 붙어 엉뚱한 쪽이 열렸다.
 */

import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useScopedState } from './scopedState'

const NONE: string[] = []

function setup(scope: string) {
  return renderHook(({ of }: { of: string }) => useScopedState(of, NONE), {
    initialProps: { of: scope },
  })
}

describe('useScopedState', () => {
  it('같은 질의에서는 보통 상태처럼 쌓인다', () => {
    const { result } = setup('a')
    act(() => { result.current[1]((c) => [...c, 'cursor-1']) })
    act(() => { result.current[1]((c) => [...c, 'cursor-2']) })
    expect(result.current[0]).toEqual(['cursor-1', 'cursor-2'])
  })

  it('질의가 바뀐 그 렌더부터 처음 값이다', () => {
    const { result, rerender } = setup('a')
    act(() => { result.current[1](['cursor-1']) })

    rerender({ of: 'b' })

    // effect 로 비우면 한 번은 옛 커서로 그려지고, 그 한 번에 옛 커서로
    // 조회가 나간다. 여기서는 바뀐 렌더에서 이미 비어 있어야 한다.
    expect(result.current[0]).toBe(NONE)
  })

  it('새 질의에서 쌓는 것은 옛 값 위가 아니라 처음 값 위다', () => {
    const { result, rerender } = setup('a')
    act(() => { result.current[1](['cursor-1']) })
    rerender({ of: 'b' })

    act(() => { result.current[1]((c) => [...c, 'b-cursor']) })

    expect(result.current[0]).toEqual(['b-cursor'])
  })

  it('옛 질의로 돌아와도 옛 값이 살아나지 않는다', () => {
    // 뒤로 가기가 이것이다. 오래전에 고른 줄이 고른 채로 살아나면 그것이
    // 일괄 편집에 실린다.
    const { result, rerender } = setup('a')
    act(() => { result.current[1](['picked-in-a']) })

    rerender({ of: 'b' })
    rerender({ of: 'a' })

    expect(result.current[0]).toBe(NONE)
  })
})
