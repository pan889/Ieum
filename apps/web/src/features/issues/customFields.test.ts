import { describe, expect, it } from 'vitest'

import type { FieldDefinition } from '@ieum/api-client'

import {
  asInputText,
  asStringList,
  changedFields,
  displayValue,
  isKnownKind,
  maxLengthOf,
  optionsOf,
  parseNumber,
  parseText,
  rangeOf,
  toggleOption,
} from './customFields'

function definition(kind: string, config: Record<string, unknown> = {}): FieldDefinition {
  return {
    id: 'f1',
    key: 'field',
    name: 'Field',
    kind,
    description: null,
    config,
    is_required: false,
    position: 0,
  }
}

describe('parseNumber', () => {
  it('0 을 비움으로 접지 않는다', () => {
    expect(parseNumber('0')).toBe(0)
  })

  it('빈 칸은 비움이다', () => {
    expect(parseNumber('')).toBeNull()
    expect(parseNumber('   ')).toBeNull()
  })

  it('숫자가 아니면 비움이다', () => {
    expect(parseNumber('abc')).toBeNull()
  })

  it('소수와 음수를 받는다', () => {
    expect(parseNumber('-2.5')).toBe(-2.5)
  })
})

describe('parseText', () => {
  it('앞뒤 공백을 떼고, 공백만 남으면 비움이다', () => {
    expect(parseText('  hi  ')).toBe('hi')
    expect(parseText('   ')).toBeNull()
  })
})

describe('toggleOption', () => {
  it('고른 순서를 유지한다', () => {
    // 서버가 순서를 보존하므로 정렬해 버리면 왕복 후 값이 달라진다.
    let value: string[] = []
    value = toggleOption(value, 'c')
    value = toggleOption(value, 'a')
    value = toggleOption(value, 'b')
    expect(value).toEqual(['c', 'a', 'b'])
  })

  it('다시 누르면 뺀다', () => {
    expect(toggleOption(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
  })
})

describe('config 읽기', () => {
  it('선택지가 없거나 깨져 있으면 빈 목록이다', () => {
    expect(optionsOf(definition('select'))).toEqual([])
    expect(optionsOf(definition('select', { options: 'nope' }))).toEqual([])
  })

  it('선택지를 문자열로 정규화한다', () => {
    expect(optionsOf(definition('select', { options: [1, 'b'] }))).toEqual(['1', 'b'])
  })

  it('min/max 가 숫자가 아니면 무시한다', () => {
    expect(rangeOf(definition('number', { min: 'x', max: 10 }))).toEqual({ max: 10 })
  })

  it('max_length 기본값은 서버와 같은 4000 이다', () => {
    expect(maxLengthOf(definition('text'))).toBe(4000)
    expect(maxLengthOf(definition('text', { max_length: 20 }))).toBe(20)
  })
})

describe('asStringList / asInputText', () => {
  it('배열이 아니면 빈 목록이다', () => {
    expect(asStringList(null)).toEqual([])
    expect(asStringList('a')).toEqual([])
  })

  it('문자열이 아닌 항목은 버린다', () => {
    expect(asStringList(['a', 3, null])).toEqual(['a'])
  })

  it('숫자를 입력 칸 문자열로 바꾼다', () => {
    expect(asInputText(0)).toBe('0')
    expect(asInputText(null)).toBe('')
    expect(asInputText(true)).toBe('')
  })
})

describe('changedFields', () => {
  it('안 바뀐 값은 빼고 보낸다', () => {
    expect(changedFields({ a: 'x', b: 2 }, { a: 'x', b: 3 })).toEqual({ b: 3 })
  })

  it('저장된 적 없는 키와 null 을 같게 본다', () => {
    // 서버에는 값이 없는 키가 아예 존재하지 않는다. 비움을 다시 비워도
    // 변경이 아니다.
    expect(changedFields({}, { a: null })).toEqual({})
  })

  it('값을 지우는 것은 변경이다', () => {
    expect(changedFields({ a: 'x' }, { a: null })).toEqual({ a: null })
  })

  it('false 와 0 을 비움으로 접지 않는다', () => {
    expect(changedFields({ flag: true, n: 1 }, { flag: false, n: 0 })).toEqual({
      flag: false,
      n: 0,
    })
  })

  it('목록은 순서까지 비교한다', () => {
    expect(changedFields({ tags: ['a', 'b'] }, { tags: ['a', 'b'] })).toEqual({})
    expect(changedFields({ tags: ['a', 'b'] }, { tags: ['b', 'a'] })).toEqual({
      tags: ['b', 'a'],
    })
  })
})

describe('displayValue', () => {
  it('bool 을 기호로 보여준다', () => {
    expect(displayValue('bool', true)).toBe('✓')
    expect(displayValue('bool', false)).toBe('—')
  })

  it('다중 선택을 쉼표로 잇는다', () => {
    expect(displayValue('multi_select', ['a', 'b'])).toBe('a, b')
  })

  it('user 는 이름으로 바꾸고, 모르면 UUID 를 그대로 둔다', () => {
    const resolve = (kind: string, id: string) => (id === 'u1' ? `${kind}:name` : undefined)
    expect(displayValue('user', 'u1', resolve)).toBe('user:name')
    expect(displayValue('user', 'u2', resolve)).toBe('u2')
  })

  it('비어 있으면 빈 문자열이다', () => {
    expect(displayValue('text', null)).toBe('')
  })
})

describe('isKnownKind', () => {
  it('서버 밸리데이터에 있는 종류를 모두 안다', () => {
    for (const kind of [
      'text',
      'number',
      'date',
      'select',
      'multi_select',
      'user',
      'version',
      'bool',
      'url',
    ]) {
      expect(isKnownKind(kind)).toBe(true)
    }
    expect(isKnownKind('nope')).toBe(false)
  })
})
