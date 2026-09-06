import type { CurrentUser } from '@ieum/api-client'
import { create } from 'zustand'

/**
 * 인증 상태.
 *
 * 서버 데이터는 TanStack Query 가, 클라이언트 상태는 zustand 가 맡는다
 * (conventions.md). 여기 있는 건 "지금 어느 단계인가"뿐이다.
 */
export type AuthStage =
  | 'unknown' // 부팅 중, 아직 모른다
  | 'anonymous' // 로그인 필요
  | 'mfa-required' // 비밀번호는 통과, 2FA 남음
  | 'mfa-enroll' // 2FA 를 등록해야 함
  | 'authenticated'

interface AuthState {
  stage: AuthStage
  user: CurrentUser | null
  setStage: (stage: AuthStage) => void
  setUser: (user: CurrentUser) => void
  reset: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  stage: 'unknown',
  user: null,
  setStage: (stage) => { set({ stage }); },
  setUser: (user) => { set({ user, stage: 'authenticated' }); },
  reset: () => { set({ stage: 'anonymous', user: null }); },
}))
