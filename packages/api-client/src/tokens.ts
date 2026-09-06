/**
 * 토큰 보관.
 *
 * 액세스 토큰은 메모리에만 둔다 — localStorage 에 두면 XSS 한 방에 털린다.
 * 리프레시 토큰은 새로고침을 견뎌야 해서 sessionStorage 를 쓴다. 완벽하진
 * 않지만 탭을 닫으면 사라지고, 서버가 로테이션·재사용 탐지로 받쳐준다.
 * (진짜 해법은 httpOnly 쿠키이고 M1 에서 전환한다 — auth.md 1절)
 */
const REFRESH_KEY = 'ieum.refresh'

export interface TokenPair {
  accessToken: string
  refreshToken: string
}

let accessToken: string | null = null

export const tokenStore = {
  get access(): string | null {
    return accessToken
  },

  get refresh(): string | null {
    try {
      return sessionStorage.getItem(REFRESH_KEY)
    } catch {
      return null
    }
  },

  set({ accessToken: access, refreshToken }: TokenPair): void {
    accessToken = access
    try {
      sessionStorage.setItem(REFRESH_KEY, refreshToken)
    } catch {
      /* 프라이빗 모드 등. 메모리 토큰만으로도 현재 탭은 동작한다. */
    }
  },

  clear(): void {
    accessToken = null
    try {
      sessionStorage.removeItem(REFRESH_KEY)
    } catch {
      /* 무시 */
    }
  },
}
