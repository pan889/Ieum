import type { ApiClient } from './client'
import { tokenStore } from './tokens'

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  mfa_required: boolean
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

    enrollTotp: () => client.post<TotpEnrollment>(`${BASE}/mfa/totp/enroll`),
    confirmTotp: (credentialId: string, code: string) =>
      client.post<void>(`${BASE}/mfa/totp/${credentialId}/confirm`, { code }),
    verifyMfa: (code: string) => client.post<void>(`${BASE}/mfa/verify`, { code }),
    issueBackupCodes: () =>
      client.post<{ codes: string[] }>(`${BASE}/mfa/backup-codes`),
  }
}

export type AuthApi = ReturnType<typeof createAuthApi>
