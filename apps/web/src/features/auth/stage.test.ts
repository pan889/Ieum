import { describe, expect, it } from 'vitest'

import { stageFor } from './stage'

describe('stageFor', () => {
  it('아무것도 안 걸려 있으면 그냥 들어간다', () => {
    expect(stageFor({ required: false, enrollmentRequired: false })).toBe('authenticated')
  })

  it('등록된 인증기가 있으면 코드를 묻는다', () => {
    expect(stageFor({ required: true, enrollmentRequired: false })).toBe('mfa-required')
  })

  it('강제인데 등록이 없으면 **등록**으로 보낸다', () => {
    // 확인 화면으로 보내면 만들 수 없는 코드를 요구받고 계정이 잠긴다.
    // 이 한 갈래가 비어 있어서 등록 화면이 통째로 도달 불가였다.
    expect(stageFor({ required: true, enrollmentRequired: true })).toBe('mfa-enroll')
  })

  it('등록이 확인보다 먼저다', () => {
    // 둘 다 참인 경우가 정확히 위험한 경우다. 순서를 뒤집으면 락아웃이다.
    expect(stageFor({ required: true, enrollmentRequired: true })).not.toBe('mfa-required')
  })
})
