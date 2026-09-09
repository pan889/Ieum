import { describe, expect, it } from 'vitest'

import type { AssetType } from '@ieum/api-client'

import { filterChoices, usableTypes } from './AssetsScreen'

function type(over: Partial<AssetType> = {}): AssetType {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: '프로젝터',
    icon: null,
    position: 0,
    is_archived: false,
    ...over,
  }
}

const archived = (name: string) => `${name} (보관)`

describe('usableTypes', () => {
  it('보관한 종류는 새 자산에 붙이지 않는다', () => {
    const rows = [type({ id: 'a', name: '노트북' }), type({ id: 'b', name: '구형 빔', is_archived: true })]
    expect(usableTypes(rows).map((row) => row.id)).toEqual(['a'])
  })
})

describe('filterChoices', () => {
  it('보관한 종류도 걸러 보기에 남는다', () => {
    /**
     * **이 시험이 이 함수가 따로 있는 이유다.** 등록용 목록을 필터에도 쓰면,
     * 종류를 보관한 순간 그 종류의 자산 수백 대가 목록에서 좁혀지지 않는다.
     * 자산은 그대로 있는데 손잡이만 사라진다.
     */
    const rows = [type({ id: 'a', name: '노트북' }), type({ id: 'b', name: '구형 빔', is_archived: true })]
    expect(filterChoices(rows, archived).map((choice) => choice.id)).toEqual(['a', 'b'])
  })

  it('보관한 종류라고 말해 준다', () => {
    // 안 쓰는 종류가 목록에 섞여 있으면, 왜 있는지 알 수 있어야 한다.
    const rows = [type({ id: 'b', name: '구형 빔', is_archived: true })]
    expect(filterChoices(rows, archived)).toEqual([{ id: 'b', label: '구형 빔 (보관)' }])
  })

  it('쓰는 종류의 이름은 그대로다', () => {
    expect(filterChoices([type({ id: 'a', name: '노트북' })], archived)).toEqual([
      { id: 'a', label: '노트북' },
    ])
  })
})
