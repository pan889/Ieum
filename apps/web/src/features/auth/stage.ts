/**
 * 로그인 뒤 어느 화면으로 갈지 (auth.md 3절).
 *
 * 순수 함수로 뺀 이유가 있다. 이 판단이 **한 갈래 비어 있었다**: 등록이
 * 필요한 사람과 확인이 필요한 사람을 같이 묶어, 아직 인증기를 등록하지도
 * 않은 사람에게 "코드를 넣으라" 는 화면을 띄웠다. 만들 수 없는 코드라
 * 그 계정은 다시 못 들어온다.
 *
 * 브라우저 테스트로 잡으려면 조직 전체 스위치를 켜야 하는데, 그건 켜는
 * 순간 뒤따르는 모든 테스트가 같은 화면에 갇히는 지뢰다(실제로 한 번
 * 밟았다 — 53개가 무너졌다). 판단만 떼어 두면 전역 상태 없이 잠글 수 있다.
 */

import type { AuthStage } from './store'

/** 서버가 알려 준 2FA 상태. 로그인 응답과 `/auth/me` 의 에러가 같은 뜻을 준다. */
export interface MfaState {
  /** 2FA 를 통과해야 일반 API 가 열린다. */
  required: boolean
  /** 강제인데 등록된 자격증명이 없다. 확인이 아니라 **등록**이 먼저다. */
  enrollmentRequired: boolean
}

/**
 * 등록이 확인보다 먼저다.
 *
 * 둘 다 참일 수 있다 — 강제이면서 아직 등록이 없는 경우가 그렇고, 그게
 * 정확히 위험한 경우다. 순서를 뒤집으면 락아웃으로 돌아간다.
 */
export function stageFor(state: MfaState): AuthStage {
  if (state.enrollmentRequired) return 'mfa-enroll'
  if (state.required) return 'mfa-required'
  return 'authenticated'
}
