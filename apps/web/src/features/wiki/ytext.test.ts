/**
 * `Y.Text` 와 textarea 사이의 순수 계산 (B16).
 *
 * 이 계산이 틀리면 브라우저에서는 "가끔 글자가 튄다" 로만 보이고, 그 증상으로
 * 원인을 찾을 수는 없다. 그래서 값을 손으로 못 박는다.
 */

import { describe, expect, it } from 'vitest'

import { _absolute } from './collab'
import { shiftCaret, shiftRange, singleEdit, type Edit } from './ytext'

describe('singleEdit', () => {
  it('바뀐 것이 없으면 null 이다', () => {
    // 빈 편집을 돌려주면 부르는 쪽이 아무 일도 안 하는 트랜잭션을 열고,
    // 그것이 다른 사람에게 방송된다.
    expect(singleEdit('같다', '같다')).toBeNull()
  })

  it('가운데에 한 글자를 넣은 것을 그 한 글자로 본다', () => {
    // **전체 교체로 보면 안 된다.** 매 타이핑이 문서 전체를 다시 쓰는
    // 편집이 되고, 같은 순간 옆 사람이 친 글자가 사라진다.
    expect(singleEdit('가나', '가X나')).toEqual({ at: 1, remove: 0, insert: 'X' })
  })

  it('한 글자를 지운 것을 그 한 글자로 본다', () => {
    expect(singleEdit('가X나', '가나')).toEqual({ at: 1, remove: 1, insert: '' })
  })

  it('구간을 골라 바꾼 것을 한 번의 편집으로 본다', () => {
    expect(singleEdit('앞 가운데 뒤', '앞 XYZ 뒤')).toEqual({
      at: 2,
      remove: 3,
      insert: 'XYZ',
    })
  })

  it('맨 앞에 넣는 것', () => {
    expect(singleEdit('나', '가나')).toEqual({ at: 0, remove: 0, insert: '가' })
  })

  it('맨 뒤에 붙이는 것', () => {
    expect(singleEdit('가', '가나')).toEqual({ at: 1, remove: 0, insert: '나' })
  })

  it('통째로 지우는 것', () => {
    // '있던 글' 은 네 글자다(있·던·공백·글). 바이트가 아니라 글자로 센다.
    expect(singleEdit('있던 글', '')).toEqual({ at: 0, remove: 4, insert: '' })
  })

  it('빈 문서에 붙여넣는 것', () => {
    expect(singleEdit('', '붙여넣은 글')).toEqual({ at: 0, remove: 0, insert: '붙여넣은 글' })
  })

  it('같은 글자가 반복돼도 앞뒤 껍질을 겹쳐 벗기지 않는다', () => {
    // `aaa` → `aa` 에서 앞 2개와 뒤 2개를 각각 벗기면 4글자를 벗긴 셈이 되어
    // 지울 길이가 음수가 된다. 껍질은 남은 길이를 넘을 수 없다.
    const edit = singleEdit('aaa', 'aa') as Edit
    expect(edit.remove).toBe(1)
    expect(edit.insert).toBe('')
    expect(edit.at + edit.remove).toBeLessThanOrEqual(3)
  })

  it('적용하면 실제로 그 문자열이 된다', () => {
    const cases: [string, string][] = [
      ['', 'x'],
      ['x', ''],
      ['가나다', '가라다'],
      ['짧다', '아주 길어진 글이다'],
      ['aaa', 'aa'],
      ['abcabc', 'abc'],
      ['한 줄\n두 줄', '한 줄\n\n두 줄'],
    ]
    for (const [before, after] of cases) {
      const edit = singleEdit(before, after)
      const applied =
        edit === null
          ? before
          : before.slice(0, edit.at) + edit.insert + before.slice(edit.at + edit.remove)
      expect(applied).toBe(after)
    }
  })
})

describe('shiftCaret', () => {
  const insertBefore: Edit = { at: 0, remove: 0, insert: '앞에 넣음' }

  it('내 앞에서 넣으면 그만큼 밀린다', () => {
    // 안 밀리면 위에서 남이 한 줄 쓸 때마다 내 커서가 뒤로 밀린다 — 같이
    // 쓰는 자리를 가장 빨리 포기하게 되는 이유다.
    expect(shiftCaret(10, insertBefore)).toBe(15)
  })

  it('내 뒤에서 일어난 편집은 나를 움직이지 않는다', () => {
    expect(shiftCaret(3, { at: 5, remove: 2, insert: 'xx' })).toBe(3)
  })

  it('내 자리에서 시작하는 편집도 나를 움직이지 않는다', () => {
    // 내가 있는 자리에 남이 글자를 넣으면 그 글자는 내 뒤에 놓인다.
    expect(shiftCaret(4, { at: 4, remove: 0, insert: 'x' })).toBe(4)
  })

  it('내 앞을 지우면 그만큼 당겨진다', () => {
    expect(shiftCaret(10, { at: 2, remove: 3, insert: '' })).toBe(7)
  })

  it('나를 물고 지우면 지운 구간의 시작으로 간다', () => {
    // 지워진 글자 가운데를 가리키고 있을 수는 없다.
    expect(shiftCaret(5, { at: 3, remove: 6, insert: '' })).toBe(3)
  })

  it('나를 물고 바꾸면 넣은 글자의 끝으로 간다', () => {
    expect(shiftCaret(5, { at: 3, remove: 6, insert: 'XY' })).toBe(5)
  })
})

describe('shiftRange', () => {
  it('선택 구간의 양 끝을 함께 옮긴다', () => {
    expect(shiftRange({ start: 4, end: 9 }, { at: 0, remove: 0, insert: 'ab' })).toEqual({
      start: 6,
      end: 11,
    })
  })

  it('구간 안쪽에서 일어난 편집은 끝만 움직인다', () => {
    expect(shiftRange({ start: 2, end: 8 }, { at: 4, remove: 0, insert: 'xxx' })).toEqual({
      start: 2,
      end: 11,
    })
  })
})

describe('_absolute', () => {
  it('API 오리진이 따로면 그쪽으로 붙는다', () => {
    // **개발 스택이 이 모양이다.** 화면은 :5173, API 는 :8000 이고, REST 가
    // 직접 :8000 으로 간다. 소켓만 :5173 으로 보내면 화면 서버의 프록시가
    // 자기 자신을 가리켜 ECONNREFUSED 로 죽는다 — 실제로 그렇게 막혔다.
    expect(_absolute('/api/v1/x/collab?ticket=t', 'http://localhost:8000', 'http://localhost:5173'))
      .toBe('ws://localhost:8000/api/v1/x/collab?ticket=t')
  })

  it('같은 오리진이면 화면의 오리진을 쓴다', () => {
    // 운영은 앞단이 하나라 이 값이 비어 있다.
    expect(_absolute('/api/v1/x/collab', '', 'https://ieum.example.com'))
      .toBe('wss://ieum.example.com/api/v1/x/collab')
  })

  it('https 는 wss 가 된다', () => {
    expect(_absolute('/p', 'https://api.example.com', 'http://x')).toBe('wss://api.example.com/p')
  })

  it('끝의 슬래시를 겹치지 않는다', () => {
    expect(_absolute('/p', 'http://api.example.com/', 'http://x')).toBe('ws://api.example.com/p')
  })
})
