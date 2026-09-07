import type { ApiClient } from './client'

// ── 폼 정의 ─────────────────────────────────────────────────────

/**
 * 요청 유형 폼의 필드 하나. **종류(`kind`)가 없다.**
 *
 * 값의 종류는 커스텀 필드 정의가 갖고 있다. 여기에 또 두면 두 벌이 되고,
 * 어긋나는 순간 폼은 저장되는데 이슈가 그 값을 못 받는다. 고객이 보는
 * 폼(`PortalFormField`)에는 서버가 정의에서 읽어 채워 준다.
 */
export interface FormFieldSpec {
  key: string
  /** 고객이 읽는 글자. **관리자가 입력한 데이터**라 번역하지 않는다. */
  label: string
  help?: string | null
  required?: boolean
}

export interface FormSchema {
  fields: FormFieldSpec[]
}

// ── 포털 정의 (내부 관리) ───────────────────────────────────────

export interface Portal {
  id: string
  project_id: string
  name: string
  slug: string
  description: string | null
  theme: Record<string, unknown>
  is_public: boolean
  is_archived: boolean
  request_type_count: number
}

export interface NewPortal {
  project_id: string
  name: string
  slug: string
  description?: string
  theme?: Record<string, unknown>
  is_public?: boolean
}

export interface PortalPatch {
  name?: string
  description?: string
  theme?: Record<string, unknown>
  is_public?: boolean
}

export interface RequestType {
  id: string
  portal_id: string
  issue_type_id: string
  issue_type_name: string
  name: string
  description: string | null
  icon: string | null
  position: number
  form_schema: FormSchema
  field_mapping: Record<string, string>
  is_enabled: boolean
  is_archived: boolean
  ticket_count: number
}

export interface NewRequestType {
  issue_type_id: string
  name: string
  description?: string
  icon?: string
  position?: number
  form_schema?: FormSchema
  field_mapping?: Record<string, string>
  is_enabled?: boolean
}

/** `issue_type_id` 가 없다 — 유형을 갈아 끼우면 폼 매핑이 통째로 무의미해진다. */
export interface RequestTypePatch {
  name?: string
  description?: string
  icon?: string
  position?: number
  form_schema?: FormSchema
  field_mapping?: Record<string, string>
  is_enabled?: boolean
}

// ── 고객 조직 ───────────────────────────────────────────────────

export interface CustomerOrg {
  id: string
  name: string
  domains: string[]
  note: string | null
  is_archived: boolean
  member_count: number
}

export interface CustomerOrgPage {
  items: CustomerOrg[]
  next_cursor: string | null
}

export interface CustomerMember {
  user_id: string
  email: string
  display_name: string
  status: string
}

// ── 고객이 보는 포털 ───────────────────────────────────────────

export interface PortalInfo {
  slug: string
  name: string
  description: string | null
  theme: Record<string, unknown>
  /** 로그인 없이 요청을 낼 수 있는가. 목록 공개 스위치가 아니다. */
  allows_guests: boolean
}

export interface PortalRequestType {
  id: string
  name: string
  description: string | null
  icon: string | null
}

export interface PortalFormField {
  key: string
  label: string
  help: string | null
  required: boolean
  /** 커스텀 필드 종류, 또는 예약 키의 `text`/`markdown`. */
  kind: string
  config: Record<string, unknown>
}

export interface PortalForm {
  request_type: PortalRequestType
  fields: PortalFormField[]
}

/**
 * 제출된 답 하나. **라벨이 함께 온다.**
 *
 * 서버가 폼 스키마를 갖고 있으므로 라벨도 서버가 준다. 화면이 `key` 만 받아
 * 요청 유형을 다시 불러 짜맞추면, 폼이 꺼진 뒤에는 라벨을 잃는다.
 */
export interface Answer {
  key: string
  label: string
  value: unknown
}

export interface Ticket {
  id: string
  key: string
  summary: string
  description: string | null
  state_name: string
  state_category: string
  created_at: string
  updated_at: string
  request_type_name: string | null
  /** 폼 키로 되돌린 답. 커스텀 필드 키가 아니고, 폼에 적힌 순서다. */
  answers: Answer[]
}

export interface TicketSummary {
  id: string
  key: string
  summary: string
  state_name: string
  state_category: string
  created_at: string
  updated_at: string
  request_type_name: string | null
}

export interface TicketPage {
  items: TicketSummary[]
  next_cursor: string | null
}

/**
 * 대화 한 줄. **`is_internal` 이 없다.**
 *
 * 이 표면에 오는 것은 언제나 공개 코멘트다. 필드를 두면 화면이 그 값을
 * 보고 무언가를 그리게 되고, 언젠가 True 가 실려 나간다. 내부 노트는
 * 상담원 화면의 `issuesApi.comments` 로만 온다.
 */
export interface Reply {
  id: string
  author_id: string | null
  body: string
  created_at: string
  edited_at: string | null
}

/** 요청을 낸 사람. `verified=false` 면 게스트가 적어 낸, 검증되지 않은 주소다. */
export interface Requester {
  user_id: string | null
  display_name: string
  email: string
  verified: boolean
}

/** 상담원 화면이 쓰는 데스크 정보. */
export interface AgentTicket {
  issue_id: string
  channel: string
  request_type_name: string | null
  portal_slug: string | null
  requester: Requester | null
  organization_name: string | null
  csat_score: number | null
}

/**
 * `ticket` 이 `null` 이면 이 이슈는 티켓이 아니다 — **오류가 아니라 답이다.**
 *
 * 봉투로 감싸는 것은 화면이 확인을 건너뛸 수 없게 하려는 것이다. 필드가
 * 널 가능이므로 `ticket.requester` 를 바로 읽는 코드는 `tsc` 가 거절한다.
 */
export interface AgentTicketEnvelope {
  ticket: AgentTicket | null
}

/**
 * 조건 기반 티켓 목록 (C3). 저장하는 것은 IQL **원문** 하나다.
 *
 * `visible_role_ids` 가 없는 것이 의도다: 큐에서 가려도 그 티켓은 이슈
 * 목록·검색으로 그대로 열리므로, 접근 제어처럼 읽히는데 아무 것도 막지 않는
 * 손잡이가 된다.
 */
export interface Queue {
  id: string
  project_id: string
  name: string
  iql: string
  position: number
}

/** 큐 한 줄. 고객이 보는 `TicketSummary` 와 달리 우선순위가 있다. */
export interface QueueTicket {
  id: string
  key: string
  summary: string
  state_name: string
  state_category: string
  priority: number
  created_at: string
  updated_at: string
}

export interface QueueTicketPage {
  items: QueueTicket[]
  next_cursor: string | null
  total: number | null
}

/** 정형 응답 (C10). 상담원이 코멘트에 끼워 넣는 조각이다. */
export interface CannedResponse {
  id: string
  project_id: string
  name: string
  body: string
  shortcut: string | null
}

export interface NewQueue {
  project_id: string
  name: string
  iql: string
  position?: number
}

export interface QueuePatch {
  name?: string
  iql?: string
  position?: number
}

export interface NewCannedResponse {
  project_id: string
  name: string
  body: string
  shortcut?: string | null
}

export interface CannedResponsePatch {
  name?: string
  body?: string
  shortcut?: string | null
  /**
   * 단축어를 **지운다.** `shortcut: null` 을 "지워라" 로 읽지 않는 이유는
   * 이름만 고치려는 요청이 단축어를 함께 날리기 때문이다.
   */
  clear_shortcut?: boolean
}

const PORTALS = '/api/v1/portals'
const CUSTOMERS = '/api/v1/customer-organizations'
const QUEUES = '/api/v1/queues'
const CANNED = '/api/v1/canned-responses'
/** 고객 표면. 이 접두사만 고객 격리를 통과한다 (auth.md 5절). */
const PORTAL = '/api/v1/portal'

export function createDeskApi(client: ApiClient) {
  return {
    // 포털 정의
    listPortals: (projectId: string, includeArchived = false) =>
      client.get<Portal[]>(
        `${PORTALS}?project_id=${projectId}${includeArchived ? '&include_archived=true' : ''}`,
      ),
    getPortal: (portalId: string) => client.get<Portal>(`${PORTALS}/${portalId}`),
    createPortal: (body: NewPortal) => client.post<Portal>(PORTALS, body),
    updatePortal: (portalId: string, body: PortalPatch) =>
      client.patch<Portal>(`${PORTALS}/${portalId}`, body),
    archivePortal: (portalId: string) => client.post<Portal>(`${PORTALS}/${portalId}/archive`),
    restorePortal: (portalId: string) =>
      client.delete<Portal>(`${PORTALS}/${portalId}/archive`),

    // 요청 유형
    listRequestTypes: (portalId: string) =>
      client.get<RequestType[]>(`${PORTALS}/${portalId}/request-types`),
    createRequestType: (portalId: string, body: NewRequestType) =>
      client.post<RequestType>(`${PORTALS}/${portalId}/request-types`, body),
    updateRequestType: (requestTypeId: string, body: RequestTypePatch) =>
      client.patch<RequestType>(`${PORTALS}/request-types/${requestTypeId}`, body),
    archiveRequestType: (requestTypeId: string) =>
      client.post<RequestType>(`${PORTALS}/request-types/${requestTypeId}/archive`),
    restoreRequestType: (requestTypeId: string) =>
      client.delete<RequestType>(`${PORTALS}/request-types/${requestTypeId}/archive`),

    // 고객 조직
    listOrgs: (params: { cursor?: string; limit?: number; q?: string } = {}) => {
      const query = new URLSearchParams()
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.q) query.set('q', params.q)
      const suffix = query.toString()
      return client.get<CustomerOrgPage>(`${CUSTOMERS}${suffix ? `?${suffix}` : ''}`)
    },
    createOrg: (body: { name: string; domains?: string[]; note?: string }) =>
      client.post<CustomerOrg>(CUSTOMERS, body),
    updateOrg: (
      organizationId: string,
      body: { name?: string; domains?: string[]; note?: string },
    ) => client.patch<CustomerOrg>(`${CUSTOMERS}/${organizationId}`, body),
    archiveOrg: (organizationId: string) =>
      client.post<CustomerOrg>(`${CUSTOMERS}/${organizationId}/archive`),
    restoreOrg: (organizationId: string) =>
      client.delete<CustomerOrg>(`${CUSTOMERS}/${organizationId}/archive`),
    listMembers: (organizationId: string) =>
      client.get<CustomerMember[]>(`${CUSTOMERS}/${organizationId}/members`),
    addMember: (organizationId: string, userId: string) =>
      client.post<CustomerMember>(`${CUSTOMERS}/${organizationId}/members`, {
        user_id: userId,
      }),
    removeMember: (organizationId: string, userId: string) =>
      client.delete<void>(`${CUSTOMERS}/${organizationId}/members/${userId}`),

    inviteCustomer: (
      organizationId: string,
      body: { email: string; display_name: string },
    ) => client.post<CustomerMember>(`${CUSTOMERS}/${organizationId}/invitations`, body),
    suggestOrg: (email: string) =>
      client.get<{ organization_id: string | null }>(
        `${CUSTOMERS}/suggest?email=${encodeURIComponent(email)}`,
      ),

    /**
     * 이 설치의 창구 목록. **로그인해야 열린다.**
     *
     * 경로에 끝 슬래시가 없다(`/api/v1/portal`). 고객 격리의 접두사는
     * `/api/v1/portal/` 이라 이 경로는 접두사로 안 잡히고, 서버가 정확히
     * 일치하는 예외로 열어 둔다 — 접두사에서 슬래시를 빼면 관리 API 까지
     * 열리기 때문이다.
     */
    myPortals: () => client.get<PortalInfo[]>(PORTAL),

    // 고객이 보는 쪽. `anonymous: true` 인 것은 게스트가 401 을 받아
    // 리프레시로 끌려가지 않게 하기 위해서다 — 로그인한 적이 없다.
    portalInfo: (slug: string) =>
      client.get<PortalInfo>(`${PORTAL}/${slug}`, { anonymous: true }),
    portalRequestTypes: (slug: string) =>
      client.get<PortalRequestType[]>(`${PORTAL}/${slug}/request-types`, { anonymous: true }),
    portalForm: (slug: string, requestTypeId: string) =>
      client.get<PortalForm>(`${PORTAL}/${slug}/request-types/${requestTypeId}`, {
        anonymous: true,
      }),
    submit: (slug: string, body: { request_type_id: string; answers: Record<string, unknown> }) =>
      client.post<Ticket>(`${PORTAL}/${slug}/requests`, body),
    submitAsGuest: (
      slug: string,
      body: {
        request_type_id: string
        email: string
        name: string
        answers: Record<string, unknown>
      },
    ) => client.post<Ticket>(`${PORTAL}/${slug}/guest-requests`, body, { anonymous: true }),
    myTickets: (slug: string, params: { cursor?: string; limit?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.limit) query.set('limit', String(params.limit))
      const suffix = query.toString()
      return client.get<TicketPage>(`${PORTAL}/${slug}/requests${suffix ? `?${suffix}` : ''}`)
    },
    myTicket: (slug: string, issueId: string) =>
      client.get<Ticket>(`${PORTAL}/${slug}/requests/${issueId}`),
    replies: (slug: string, issueId: string) =>
      client.get<Reply[]>(`${PORTAL}/${slug}/requests/${issueId}/replies`),
    reply: (slug: string, issueId: string, body: string) =>
      client.post<Reply>(`${PORTAL}/${slug}/requests/${issueId}/replies`, { body }),

    /**
     * 이 이슈의 데스크 정보. **티켓이 아니면 `ticket: null` 이다.**
     *
     * 200 이다. 상담원은 평범한 이슈를 하루에 수십 번 열고, 그 때마다
     * 404 가 찍히면 콘솔은 못 읽는 것이 된다.
     */
    agentTicket: (issueId: string) =>
      client.get<AgentTicketEnvelope>(`/api/v1/tickets/${issueId}`),

    // ── 큐 (C3) ─────────────────────────────────────────────
    listQueues: (projectId: string) =>
      client.get<Queue[]>(`${QUEUES}?project_id=${encodeURIComponent(projectId)}`),
    createQueue: (body: NewQueue) => client.post<Queue>(QUEUES, body),
    updateQueue: (id: string, body: QueuePatch) => client.patch<Queue>(`${QUEUES}/${id}`, body),
    deleteQueue: (id: string) => client.delete<void>(`${QUEUES}/${id}`),
    /** 큐를 돌린다. **실행자 권한으로** 돈다 — 큐가 권한을 넓히지 않는다. */
    runQueue: (id: string, params: { limit?: number; cursor?: string } = {}) => {
      const query = new URLSearchParams()
      if (params.limit !== undefined) query.set('limit', String(params.limit))
      if (params.cursor !== undefined) query.set('cursor', params.cursor)
      const suffix = query.size > 0 ? `?${query.toString()}` : ''
      return client.get<QueueTicketPage>(`${QUEUES}/${id}/tickets${suffix}`)
    },

    // ── 정형 응답 (C10) ─────────────────────────────────────
    listCannedResponses: (projectId: string) =>
      client.get<CannedResponse[]>(`${CANNED}?project_id=${encodeURIComponent(projectId)}`),
    createCannedResponse: (body: NewCannedResponse) =>
      client.post<CannedResponse>(CANNED, body),
    updateCannedResponse: (id: string, body: CannedResponsePatch) =>
      client.patch<CannedResponse>(`${CANNED}/${id}`, body),
    deleteCannedResponse: (id: string) => client.delete<void>(`${CANNED}/${id}`),
  }
}

export type DeskApi = ReturnType<typeof createDeskApi>
