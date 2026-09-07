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


/**
 * 권한 하나의 정의.
 *
 * 표시 이름이 **없다.** 서버는 키만 주고 화면이 `admin:permission.<키>` 로
 * 번역한다 — 서버는 화면의 언어를 모른다(i18n.md 1절).
 */
export interface PermissionDef {
  key: string
  /** 이 권한을 붙일 수 있는 스코프. 역할 편집이 걸러 쓴다. */
  scope_kinds: string[]
  /** 참이면 2FA 를 방금 통과한 세션만 쓸 수 있다. */
  requires_step_up: boolean
}

/** 관리 화면용 역할. 목록에 필요한 곁가지까지 담는다. */
export interface Role {
  id: string
  name: string
  description: string | null
  scope_kind: string
  /** 시드가 정의로 되돌리는 역할. 권한은 여기서 못 고친다. */
  is_builtin: boolean
  require_mfa: boolean
  grants: string[]
  /** 이 역할을 받은 주체 수. 지우기 전에 영향 범위를 알아야 한다. */
  assignment_count: number
}

export interface RoleAssignment {
  id: string
  role_id: string
  scope_kind: string
  scope_id: string | null
  principal_kind: string
  principal_id: string
  /** 사람이면 이메일, 그룹이면 이름. 지워진 주체면 null. */
  principal_label: string | null
}

const ROLES = '/api/v1/roles'

export function createRolesApi(client: ApiClient) {
  return {
    /** 등록된 권한 상수 전부. 토큰 스코프 고르기와 역할 편집이 쓴다. */
    permissions: () => client.get<PermissionDef[]>(`${ROLES}/permissions`),

    list: () => client.get<Role[]>(ROLES),
    create: (body: {
      name: string
      scope_kind: string
      grants: string[]
      description?: string | null
      require_mfa?: boolean
    }) => client.post<Role>(ROLES, body),

    /**
     * 권한은 **목록 전체**로 보낸다. 추가·제거를 따로 보내면 화면과 서버가
     * 서로 다른 "지금 상태" 를 들고 계산하게 되고, 어긋나는 순간 아무도 모른다.
     */
    update: (
      id: string,
      body: { grants?: string[]; description?: string | null; require_mfa?: boolean },
    ) => client.patch<Role>(`${ROLES}/${id}`, body),
    remove: (id: string) => client.delete<void>(`${ROLES}/${id}`),

    assignments: (id: string) => client.get<RoleAssignment[]>(`${ROLES}/${id}/assignments`),
    assign: (body: {
      role_id: string
      scope_kind: string
      scope_id: string | null
      principal_kind: string
      principal_id: string
    }) => client.post<void>(`${ROLES}/assignments`, body),
    /** 준 것을 되돌린다. 주는 길만 있으면 그건 권한 관리가 아니다. */
    revoke: (assignmentId: string) =>
      client.delete<void>(`${ROLES}/assignments/${assignmentId}`),
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
  /** OIDC 만 채운다. */
  client_id: string | null
  saml_certificate_count: number
  saml_allow_idp_initiated: boolean
  saml_want_encrypted: boolean
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

export interface NewSamlProvider {
  name: string
  /** 붙이면 발급자·SSO 주소·인증서를 서버가 읽어 채운다. */
  metadata_xml?: string
  entity_id?: string
  sso_url?: string
  certificates?: string[]
  sp_private_key?: string
  sp_certificate?: string
  want_encrypted?: boolean
  allow_idp_initiated?: boolean
  email_attribute?: string
  name_attribute?: string
  groups_attribute?: string | null
  jit_provisioning?: boolean
  link_verified_email?: boolean
  email_domains?: string[]
}

export function createIdpApi(client: ApiClient) {
  return {
    list: () => client.get<IdentityProvider[]>('/api/v1/admin/sso/providers'),
    create: (body: NewIdentityProvider) =>
      client.post<IdentityProvider>('/api/v1/admin/sso/providers', body),
    createSaml: (body: NewSamlProvider) =>
      client.post<IdentityProvider>('/api/v1/admin/sso/saml/providers', body),
    /** 지우지 않고 끈다 — 지우면 계정 연결이 따라 사라진다. */
    disable: (id: string) =>
      client.post<void>(`/api/v1/admin/sso/providers/${id}/disable`),
    /** 다시 켠다. 이게 없으면 끄는 것이 일방통행이다. */
    enable: (id: string) =>
      client.post<void>(`/api/v1/admin/sso/providers/${id}/enable`),
  }
}

export type IdpApi = ReturnType<typeof createIdpApi>
