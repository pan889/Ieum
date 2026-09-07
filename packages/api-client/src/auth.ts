import type { ApiClient } from './client'
import { tokenStore } from './tokens'

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  mfa_required: boolean
  /** 강제인데 등록된 자격증명이 없다. 확인이 아니라 **등록**으로 가야 한다. */
  mfa_enrollment_required: boolean
}

export interface CurrentUser {
  id: string
  email: string
  display_name: string
  avatar_url: string | null
  locale: string
  timezone: string
  status: string
  is_customer: boolean
  last_login_at: string | null
}

export interface TotpEnrollment {
  credential_id: string
  secret: string
  provisioning_uri: string
  qr_svg: string
}

export interface SessionInfo {
  id: string
  ip: string | null
  user_agent: string | null
  created_at: string
  expires_at: string
  mfa_satisfied_at: string | null
  is_current: boolean
}

const BASE = '/api/v1/auth'
const USERS = '/api/v1/users'

export interface UserPage {
  items: CurrentUser[]
  next_cursor: string | null
}

/** 담당자·멘션 피커. `ids` 를 주면 그 사용자들만 돌려준다. */
export function createUsersApi(client: ApiClient) {
  return {
    list: (params: { q?: string; ids?: string[]; limit?: number; cursor?: string } = {}) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.ids?.length) query.set('ids', params.ids.join(','))
      if (params.limit) query.set('limit', String(params.limit))
      if (params.cursor) query.set('cursor', params.cursor)
      const suffix = query.size > 0 ? `?${query.toString()}` : ''
      return client.get<UserPage>(`${USERS}${suffix}`)
    },

    /**
     * 본인 설정. 언어는 서버가 기억한다 — 브라우저에만 두면 다른 기기에서
     * 다시 영어로 열리고, 알림 메일도 옛 언어로 나간다.
     */
    updateMe: (body: { locale: string }) => client.patch<CurrentUser>(`${USERS}/me`, body),

    /**
     * 초대를 받아들이고 비밀번호를 정한다.
     *
     * 아직 로그인할 수 없는 사람이 부른다 — 토큰이 곧 신분증이므로 익명으로
     * 보낸다. 여기에 Authorization 을 붙이면 로그인한 사람만 초대를 받을 수
     * 있게 된다.
     */
    acceptInvite: (body: { token: string; password: string }) =>
      client.post<CurrentUser>(`${USERS}/accept-invite`, body, { anonymous: true }),
  }
}

export type UsersApi = ReturnType<typeof createUsersApi>

export function createAuthApi(client: ApiClient) {
  return {
    async login(email: string, password: string): Promise<TokenResponse> {
      const tokens = await client.post<TokenResponse>(
        `${BASE}/login`,
        { email, password },
        { anonymous: true },
      )
      tokenStore.set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
      })
      return tokens
    },

    async logout(): Promise<void> {
      try {
        await client.post<void>(`${BASE}/logout`)
      } finally {
        tokenStore.clear()
      }
    },

    me: () => client.get<CurrentUser>(`${BASE}/me`),
    sessions: () => client.get<SessionInfo[]>(`${BASE}/sessions`),
    revokeAllSessions: () => client.delete<void>(`${BASE}/sessions`),
    /** 기기 하나만 끊는다. 전부 끊으면 지금 쓰는 자리에서도 튕겨 나간다. */
    revokeSession: (id: string) => client.delete<void>(`${BASE}/sessions/${id}`),

    enrollTotp: () => client.post<TotpEnrollment>(`${BASE}/mfa/totp/enroll`),
    confirmTotp: (credentialId: string, code: string) =>
      client.post<void>(`${BASE}/mfa/totp/${credentialId}/confirm`, { code }),
    verifyMfa: (code: string) => client.post<void>(`${BASE}/mfa/verify`, { code }),
    issueBackupCodes: () =>
      client.post<{ codes: string[] }>(`${BASE}/mfa/backup-codes`),
  }
}

export type AuthApi = ReturnType<typeof createAuthApi>
