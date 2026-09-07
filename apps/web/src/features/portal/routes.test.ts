/**
 * 포털 경로. 주소가 **공유 가능해야** 한다는 것이 이 파일의 계약이다 —
 * 폼 링크를 붙여 줄 수 있어야 하고, 뒤로 가기가 포털 밖으로 나가면 안 된다.
 */

import { describe, expect, it } from 'vitest'

import { parsePortalPath, pathFor } from './routes'

describe('parsePortalPath', () => {
  it('포털 밖은 null 이다', () => {
    // `App.tsx` 가 이 null 로 "포털이 아니다" 를 판단하지는 않는다(접두사로
    // 가른다). 그래도 여기서 null 이어야 포털 컴포넌트가 남의 주소를 자기
    // 것으로 읽지 않는다.
    expect(parsePortalPath('/projects')).toBeNull()
    expect(parsePortalPath('/portal')).toBeNull()
    expect(parsePortalPath('/portal/')).toBeNull()
  })

  it('슬러그만 있으면 첫 화면이다', () => {
    expect(parsePortalPath('/portal/help')).toEqual({ kind: 'home', slug: 'help' })
  })

  it('폼과 상세를 가른다', () => {
    expect(parsePortalPath('/portal/help/new/abc')).toEqual({
      kind: 'form',
      slug: 'help',
      requestTypeId: 'abc',
    })
    expect(parsePortalPath('/portal/help/requests/xyz')).toEqual({
      kind: 'ticket',
      slug: 'help',
      issueId: 'xyz',
    })
  })

  it('id 가 빠진 주소는 첫 화면으로 떨어진다', () => {
    // 404 를 그리지 않는다. 잘린 링크를 눌렀을 때 창구를 보여 주는 편이
    // "그런 페이지 없음" 보다 쓸모 있다.
    expect(parsePortalPath('/portal/help/new')).toEqual({ kind: 'home', slug: 'help' })
    expect(parsePortalPath('/portal/help/requests')).toEqual({ kind: 'home', slug: 'help' })
  })

  it('모르는 구획도 첫 화면이다', () => {
    expect(parsePortalPath('/portal/help/nope/1')).toEqual({ kind: 'home', slug: 'help' })
  })
})

describe('pathFor', () => {
  it('왕복한다', () => {
    // 이것이 링크를 붙여 줄 수 있다는 계약이다: 주소 → 상태 → 같은 주소.
    for (const route of [
      { kind: 'home', slug: 'help' },
      { kind: 'form', slug: 'help', requestTypeId: 'abc' },
      { kind: 'ticket', slug: 'help', issueId: 'xyz' },
    ] as const) {
      expect(parsePortalPath(pathFor(route))).toEqual(route)
    }
  })
})
