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
  /** 이 사람에게만 걸린 2FA 강제. 조직 정책과 별개다. */
  require_mfa: boolean
  last_login_at: string | null
}

/**
 * 그룹 하나.
 *
 * `source` 가 `idp` 면 IdP 가 관리한다 — 이름과 멤버는 여기서 못 고친다.
 * 화면이 그걸 모르면 편집 버튼을 그려 놓고 서버가 거절한다.
 */
export interface UserGroup {
  id: string
  name: string
  description: string | null
  source: string
  member_count: number
}

/** 등록된 2차 요소 하나. **비밀은 여기에 없다.** */
export interface MfaCredential {
  id: string
  kind: string
  label: string | null
  confirmed_at: string | null
  last_used_at: string | null
  /** 다른 기기로 동기화되는가(=패스키). 잃었을 때 결과가 다르다. */
  webauthn_backed_up: boolean
  webauthn_transports: string[]
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
const GROUPS = '/api/v1/groups'

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

    invite: (body: { email: string; display_name: string; locale?: string }) =>
      client.post<CurrentUser>(`${USERS}/invite`, body),

    /**
     * 계정을 잠근다. 세션도 함께 끊긴다 — 되살려도 옛 브라우저는 다시
     * 로그인해야 한다. step-up 대상이다(관리자가 2FA 를 통과해야 한다).
     */
    suspend: (id: string) => client.post<CurrentUser>(`${USERS}/${id}/suspend`, {}),
    reactivate: (id: string) => client.post<CurrentUser>(`${USERS}/${id}/reactivate`, {}),
    /** 초대장을 다시 보낸다. 비밀번호를 잊은 사람이 돌아오는 유일한 길이다. */
    reinvite: (id: string) => client.post<CurrentUser>(`${USERS}/${id}/reinvite`, {}),

    /** 이 사람에게만 2FA 를 강제한다. 조직 전체를 켜지 않고도 걸 수 있다. */
    setRequireMfa: (id: string, required: boolean) =>
      client.patch<CurrentUser>(`${USERS}/${id}`, { require_mfa: required }),

    /** 남의 세션을 원격으로 끊는다. 퇴사·기기 분실 때 쓴다. */
    revokeSessions: (id: string) => client.delete<void>(`${USERS}/${id}/sessions`),
  }
}

export type UsersApi = ReturnType<typeof createUsersApi>

/**
 * 그룹 관리.
 *
 * 역할을 그룹에 붙이고 사람은 그룹에 넣는다 — 권한을 사람 단위로 만지지
 * 않기 위한 자리다.
 */
export function createGroupsApi(client: ApiClient) {
  return {
    list: () => client.get<UserGroup[]>(GROUPS),
    create: (body: { name: string; description?: string | null }) =>
      client.post<UserGroup>(GROUPS, body),
    update: (id: string, body: { name?: string; description?: string | null }) =>
      client.patch<UserGroup>(`${GROUPS}/${id}`, body),
    remove: (id: string) => client.delete<void>(`${GROUPS}/${id}`),

    members: (id: string) => client.get<CurrentUser[]>(`${GROUPS}/${id}/members`),
    addMember: (id: string, userId: string) =>
      client.post<void>(`${GROUPS}/${id}/members`, { user_id: userId }),
    removeMember: (id: string, userId: string) =>
      client.delete<void>(`${GROUPS}/${id}/members/${userId}`),
  }
}

export type GroupsApi = ReturnType<typeof createGroupsApi>

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

    /** 로그인 **전에** 부른다. 실패해도 화면은 로컬 로그인으로 서야 한다. */
    ssoProviders: () =>
      client.get<SsoProvider[]>(`${BASE}/sso/providers`, { anonymous: true }),
    /** 콜백 주소는 서버가 정한다 — 여기서 고르게 하면 코드가 새 나간다. */
    startSso: (providerId: string) =>
      client.post<{ authorization_url: string }>(`${BASE}/sso/${providerId}/start`, undefined, {
        anonymous: true,
      }),

    /**
     * SAML 로그인 시작. **OIDC 와 갈린다** — 어느 쪽인지는 `provider.kind` 가
     * 말해 준다. 화면이 찍으면 다른 종류의 IdP 에서 조용히 실패한다.
     */
    startSaml: (providerId: string) =>
      client.post<{ authorization_url: string }>(
        `${BASE}/saml/${providerId}/start`,
        undefined,
        { anonymous: true },
      ),

    /**
     * ACS 가 준 1회용 코드를 세션으로 바꾼다.
     *
     * SAML 은 IdP 가 우리 서버로 직접 POST 하므로 화면이 그 응답을 읽지
     * 못한다. 서버가 코드만 주소로 넘겨 주고, 그것을 여기서 바꾼다.
     */
    async completeSaml(code: string): Promise<TokenResponse> {
      const tokens = await client.post<TokenResponse>(
        `${BASE}/saml/exchange`,
        { code },
        { anonymous: true },
      )
      // 로컬 로그인·OIDC 와 **같이** 저장한다. 빠뜨리면 교환은 성공하는데
      // 다음 요청이 401 을 받아 화면이 로그인으로 되돌아간다.
      tokenStore.set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
      })
      return tokens
    },

    async completeSso(code: string, state: string): Promise<TokenResponse> {
      const tokens = await client.post<TokenResponse>(
        `${BASE}/sso/callback`,
        { code, state },
        { anonymous: true },
      )
      // **로컬 로그인과 같이 저장한다.** 안 하면 교환은 성공하는데 다음
      // 요청이 401 을 받아, 화면은 다시 로그인 화면으로 떨어진다 — 사용자
      // 눈에는 "SSO 가 안 된다" 로만 보인다.
      tokenStore.set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
      })
      return tokens
    },

    /** 등록된 2차 요소. 백업 코드는 나오지 않는다(개수만 의미가 있다). */
    mfaCredentials: () => client.get<MfaCredential[]>(`${BASE}/mfa/credentials`),
    removeMfaCredential: (id: string) => client.delete<void>(`${BASE}/mfa/credentials/${id}`),

    /** 인증기 등록: 옵션을 받아 브라우저에 넘기고, 응답을 되돌려 준다. */
    startPasskeyRegistration: () =>
      client.post<{ options: string }>(`${BASE}/mfa/webauthn/register/start`),
    finishPasskeyRegistration: (response: string, label?: string) =>
      client.post<MfaCredential>(`${BASE}/mfa/webauthn/register/finish`, {
        response,
        ...(label ? { label } : {}),
      }),

    startPasskeyVerification: () =>
      client.post<{ options: string }>(`${BASE}/mfa/webauthn/verify/start`),
    finishPasskeyVerification: (response: string) =>
      client.post<void>(`${BASE}/mfa/webauthn/verify/finish`, { response }),

    enrollTotp: () => client.post<TotpEnrollment>(`${BASE}/mfa/totp/enroll`),
    confirmTotp: (credentialId: string, code: string) =>
      client.post<void>(`${BASE}/mfa/totp/${credentialId}/confirm`, { code }),
    verifyMfa: (code: string) => client.post<void>(`${BASE}/mfa/verify`, { code }),
    issueBackupCodes: () =>
      client.post<{ codes: string[] }>(`${BASE}/mfa/backup-codes`),
  }
}

export type AuthApi = ReturnType<typeof createAuthApi>


/** 로그인 화면이 버튼을 그릴 만큼만. 익명에게 열린 목록이다. */
export interface SsoProvider {
  id: string
  name: string
  /** 시작하는 경로가 갈린다. 'oidc' | 'saml'. */
  kind: string
}
