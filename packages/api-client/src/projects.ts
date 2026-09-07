import type { ApiClient } from './client'

export interface Project {
  id: string
  key: string
  name: string
  description: string | null
  parent_id: string | null
  lead_id: string | null
  is_public: boolean
  archived_at: string | null
  created_at: string
}

export interface ProjectPage {
  items: Project[]
  next_cursor: string | null
  total: number | null
}

export interface NewProject {
  key: string
  name: string
  description?: string
  parent_id?: string
  is_public?: boolean
}

const BASE = '/api/v1/projects'

export function createProjectsApi(client: ApiClient) {
  return {
    list: (
      params: { cursor?: string; limit?: number; includeArchived?: boolean; q?: string } = {},
    ) => {
      const query = new URLSearchParams()
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.includeArchived) query.set('include_archived', 'true')
      if (params.q) query.set('q', params.q)
      const suffix = query.size > 0 ? `?${query}` : ''
      return client.get<ProjectPage>(`${BASE}${suffix}`)
    },
    get: (id: string) => client.get<Project>(`${BASE}/${id}`),
    create: (body: NewProject) => client.post<Project>(BASE, body),
    archive: (id: string) => client.post<Project>(`${BASE}/${id}/archive`),
  }
}

export type ProjectsApi = ReturnType<typeof createProjectsApi>


export interface PermissionDef {
  key: string
  description: string
  scope_kinds: string[]
  requires_step_up: boolean
}

export function createRolesApi(client: ApiClient) {
  return {
    /** 등록된 권한 상수 전부. 토큰 스코프 고르기와 역할 편집이 쓴다. */
    permissions: () => client.get<PermissionDef[]>('/api/v1/roles/permissions'),
  }
}

export type RolesApi = ReturnType<typeof createRolesApi>


export interface SecurityPolicy {
  /** 조직 전체 2FA 강제. 정책은 **다음 로그인부터** 문다. */
  require_mfa: boolean
}

export function createSecurityApi(client: ApiClient) {
  return {
    get: () => client.get<SecurityPolicy>('/api/v1/admin/security'),
    setRequireMfa: (required: boolean) =>
      client.put<SecurityPolicy>('/api/v1/admin/security', { require_mfa: required }),
  }
}

export type SecurityApi = ReturnType<typeof createSecurityApi>


/** 관리 화면용 IdP. **시크릿은 여기에 없다** — 저장도 암호문뿐이다. */
export interface IdentityProvider {
  id: string
  name: string
  kind: string
  is_enabled: boolean
  issuer: string
  client_id: string
  jit_provisioning: boolean
  link_verified_email: boolean
  email_domains: string[]
  trust_idp_mfa: boolean
  groups_claim: string | null
}

export interface NewIdentityProvider {
  name: string
  issuer: string
  client_id: string
  client_secret: string
  authorization_endpoint: string
  token_endpoint: string
  jwks_uri: string
  scopes?: string
  email_claim?: string
  name_claim?: string
  groups_claim?: string | null
  jit_provisioning?: boolean
  link_verified_email?: boolean
  email_domains?: string[]
  trust_idp_mfa?: boolean
}

export function createIdpApi(client: ApiClient) {
  return {
    list: () => client.get<IdentityProvider[]>('/api/v1/admin/sso/providers'),
    create: (body: NewIdentityProvider) =>
      client.post<IdentityProvider>('/api/v1/admin/sso/providers', body),
    /** 지우지 않고 끈다 — 지우면 계정 연결이 따라 사라진다. */
    disable: (id: string) =>
      client.post<void>(`/api/v1/admin/sso/providers/${id}/disable`),
  }
}

export type IdpApi = ReturnType<typeof createIdpApi>
