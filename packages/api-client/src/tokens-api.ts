import type { ApiClient } from './client'

export interface ApiToken {
  id: string
  name: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface IssuedApiToken {
  token: ApiToken
  /** 평문. **발급 시 한 번만** 볼 수 있다 — 서버는 해시만 저장한다. */
  secret: string
}

const BASE = '/api/v1/tokens'

export function createApiTokensApi(client: ApiClient) {
  return {
    list: () => client.get<ApiToken[]>(BASE),
    issue: (body: { name: string; scopes: string[]; expires_in_days?: number }) =>
      client.post<IssuedApiToken>(BASE, body),
    revoke: (id: string) => client.delete<void>(`${BASE}/${id}`),
  }
}

export type ApiTokensApi = ReturnType<typeof createApiTokensApi>
