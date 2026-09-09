/**
 * 이슈에 붙은 커밋·PR 의 짧은 이름 (feature-map A22).
 *
 * 작지만 두 값을 한 열(`external_ref`)에 담고 있어서 **같은 자르기를 걸기
 * 쉽다.** 그러면 `#12345678` 이 조용히 다른 PR 을 가리킨다.
 */

import { describe, expect, it } from 'vitest'

import { shortRef } from './Development'

describe('shortRef', () => {
  it('커밋은 앞 일곱 자로 줄인다 — 사람이 그 길이로 말한다', () => {
    expect(shortRef({ kind: 'commit', external_ref: 'a'.repeat(40) })).toBe('aaaaaaa')
  })

  it('PR 번호는 자르지 않는다', () => {
    expect(shortRef({ kind: 'pull_request', external_ref: '12345678' })).toBe('#12345678')
  })

  it('짧은 SHA 를 늘리지도 채우지도 않는다', () => {
    expect(shortRef({ kind: 'commit', external_ref: 'abc12' })).toBe('abc12')
  })
})
