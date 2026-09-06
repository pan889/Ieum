import { describe, expect, it } from 'vitest'

import { splitByKeywords } from './highlight'

describe('splitByKeywords', () => {
  it('검색어를 표시 조각으로 나눈다', () => {
    expect(splitByKeywords('배포 전 롤백 계획', ['롤백'])).toEqual([
      { text: '배포 전 ', hit: false },
      { text: '롤백', hit: true },
      { text: ' 계획', hit: false },
    ])
  })

  it('여러 낱말을 모두 찾는다', () => {
    const parts = splitByKeywords('사과와 배가 있다', ['사과', '배가'])
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(['사과', '배가'])
  })

  it('대소문자를 가리지 않는다', () => {
    expect(splitByKeywords('Deploy Runbook', ['deploy'])[0]).toEqual({
      text: 'Deploy',
      hit: true,
    })
  })

  it('낱말이 없으면 통째로 한 조각', () => {
    expect(splitByKeywords('본문', [])).toEqual([{ text: '본문', hit: false }])
  })

  it('정규식 특수문자가 들어와도 패턴이 깨지지 않는다', () => {
    // 검색어는 사용자가 친 글자다. 그대로 정규식에 넣으면 여기서 터진다.
    expect(() => splitByKeywords('a (b) c', ['('])).not.toThrow()
    expect(splitByKeywords('a+b', ['a+b'])).toEqual([{ text: 'a+b', hit: true }])
  })

  it('못 찾으면 통째로 한 조각', () => {
    expect(splitByKeywords('본문', ['없는말'])).toEqual([{ text: '본문', hit: false }])
  })
})
