/**
 * 짝 목록을 만드는 규칙.
 *
 * 이 두 함수가 화면에서 빠져나와 있는 이유는 **틀렸을 때의 모양이 조용하기
 * 때문**이다. 짝을 잘못 담아 보내도 미리 보기는 멀쩡히 그려지고, 사람은 그
 * 화면을 믿고 적재를 누른다.
 */

import { describe, expect, it } from 'vitest'

import type { ImportMatch } from '@ieum/api-client'

import { nextOverrides, selectedValue } from './ImportsScreen'

function match(over: Partial<ImportMatch> = {}): ImportMatch {
  return { source: 'New', target_id: 'open-id', target_name: '열림', how: 'name', ...over }
}

describe('nextOverrides', () => {
  it('고른 짝을 담는다', () => {
    expect(nextOverrides({}, 'statuses', 'New', 'open-id')).toEqual({
      statuses: { New: 'open-id' },
    })
  })

  it('빈 칸을 고르면 그 낱말을 **뺀다** — 빈 문자열로 두지 않는다', () => {
    /**
     * 서버는 없는 id 를 가리키는 짝을 무시한다. 빈 문자열을 남겨 보내면
     * "안 고름" 으로 되돌린 것과 오타를 낸 것이 같은 요청이 되고, 미리
     * 보기는 둘을 구별해 말할 수 없다.
     */
    const before = { statuses: { New: 'open-id', Closed: 'closed-id' } }
    expect(nextOverrides(before, 'statuses', 'New', '')).toEqual({
      statuses: { Closed: 'closed-id' },
    })
  })

  it('마지막 하나를 빼면 그 갈래도 통째로 사라진다', () => {
    // 빈 표를 남기면 `{"statuses": {}}` 를 보내게 된다. 뜻이 없는 것을
    // 보내지 않는다.
    expect(nextOverrides({ statuses: { New: 'open-id' } }, 'statuses', 'New', '')).toEqual({})
  })

  it('다른 갈래는 건드리지 않는다', () => {
    const before = { types: { Bug: 'bug-id' }, statuses: { New: 'open-id' } }
    expect(nextOverrides(before, 'statuses', 'New', 'closed-id')).toEqual({
      types: { Bug: 'bug-id' },
      statuses: { New: 'closed-id' },
    })
  })

  it('같은 낱말을 다시 고르면 덮어쓴다 — 두 벌이 되지 않는다', () => {
    const before = { types: { Bug: 'bug-id' } }
    expect(nextOverrides(before, 'types', 'Bug', 'task-id')).toEqual({ types: { Bug: 'task-id' } })
  })

  it('원래 것을 바꾸지 않는다', () => {
    // 리액트 상태를 제자리에서 고치면 다시 그리지 않는다.
    const before = { types: { Bug: 'bug-id' } }
    nextOverrides(before, 'types', 'Story', 'story-id')
    expect(before).toEqual({ types: { Bug: 'bug-id' } })
  })
})

describe('selectedValue', () => {
  it('이름이 맞은 것은 서버가 준 짝을 보여 준다', () => {
    expect(selectedValue(match(), undefined)).toBe('open-id')
  })

  it('못 이은 줄은 상자가 빈 채로 남는다', () => {
    // 여기에 가드는 없다. 서버가 못 이은 줄에 늘 빈 `target_id` 를 보내기
    // 때문이다 — 그 계약은 파이썬 쪽 `match_names` 가 지킨다. 이 시험은
    // 그 계약이 화면까지 그대로 흐르는 것만 본다.
    expect(selectedValue(match({ how: 'unmatched', target_id: '', target_name: '' }), {})).toBe('')
  })

  it('사람이 고른 것이 서버가 준 것을 이긴다', () => {
    // 고른 직후 다시 그릴 때, 아직 새 미리 보기가 안 왔어도 고른 것이 보여야
    // 한다. 안 그러면 상자가 잠깐 옛 값으로 되돌아간다.
    expect(selectedValue(match(), { New: 'closed-id' })).toBe('closed-id')
  })

  it('짝이 다른 낱말의 것이면 안 쓴다', () => {
    expect(selectedValue(match(), { Closed: 'closed-id' })).toBe('open-id')
  })
})
