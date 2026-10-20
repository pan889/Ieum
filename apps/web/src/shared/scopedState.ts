import { useCallback, useState, type Dispatch, type SetStateAction } from 'react'

/**
 * `scope` 가 바뀌면 **처음 값으로 돌아가는** 상태.
 *
 * 쪽 넘김 커서·고른 줄처럼 "이 질의의 것" 인 상태가 있다. 질의를 바꾸는 길이
 * 화면 안의 단추 하나뿐이면 그 단추가 비우면 되지만, 실제로는 뒤로 가기·
 * 사이드바 링크·저장한 필터·명령 팔레트가 모두 주소를 바꾼다. 그 길마다
 * 비우기를 붙이면 하나는 반드시 빠지고, 빠진 길에서는 **옛 질의의 커서가 새
 * 질의에 붙어** 엉뚱한 쪽이 열린다. 그래서 상태 자체가 자기 질의를 기억한다.
 *
 * effect 로 비우지 않는다. effect 는 그린 **뒤**에 돌므로, 한 번은 옛 값으로
 * 그려지고 그 한 번에 옛 커서로 조회가 나간다. 여기서는 렌더하는 그 자리에서
 * 질의가 다르면 처음 값을 돌려준다.
 *
 * `initial` 은 **매번 같은 값**을 넘긴다(모듈 상수나 `null`). 렌더마다 새 배열을
 * 넘기면 그 값을 의존으로 쓰는 쪽이 매번 다시 돈다.
 */
export function useScopedState<T>(scope: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const [held, setHeld] = useState({ of: scope, value: initial })

  // 질의가 바뀌었으면 옛 값을 **버린다.** 안 버리면 그 질의로 되돌아왔을 때
  // (뒤로 가기) 오래전에 고른 줄이 살아난다. 렌더 중에 자기 상태를 고치는
  // 것은 React 가 허락하는 모양이다 — 그리던 것을 버리고 바로 다시 그린다.
  if (held.of !== scope) setHeld({ of: scope, value: initial })

  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (next) => {
      setHeld((now) => {
        const current = now.of === scope ? now.value : initial
        const value =
          typeof next === 'function' ? (next as (current: T) => T)(current) : next
        return { of: scope, value }
      })
    },
    [scope, initial],
  )

  return [held.of === scope ? held.value : initial, set]
}
