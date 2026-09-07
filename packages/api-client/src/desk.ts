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
  /** 요청 중 문서를 추천할 스페이스 (C8). 안 걸었으면 `null`. */
  kb_space_id: string | null
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
  /** 요청 중 문서를 추천할 지식베이스 스페이스 (C8). `kind = "kb"` 만. */
  kb_space_id?: string
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
  kb_space_id?: string
  /**
   * 연결을 **끊는다.** `kb_space_id: undefined` 는 "안 건드린다" 와 구별되지
   * 않는다 — 부분 수정에서 값이 없다는 것은 언제나 후자다.
   */
  clear_kb_space?: boolean
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
  /** 이 티켓에 걸린 SLA 들. 비어 있으면 정책이 없거나 아직 안 걸렸다. */
  sla: SlaStanding[]
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

/**
 * 티켓의 SLA 한 줄 (C5). **서버가 남은 시간을 계산해 준다.**
 *
 * 목표 시각만 받아 브라우저가 카운트다운하면 업무 시간이 빠진다 — 금요일
 * 저녁에 남은 4시간이 토요일 아침에 0 이 된다. 위반이면 `remaining_seconds`
 * 가 음수다: 화면이 "3시간 초과" 를 말할 수 있어야 한다.
 */
export interface SlaStanding {
  policy_name: string
  metric: string
  target_at: string
  remaining_seconds: number
  breached: boolean
  paused: boolean
  completed: boolean
}

/** 업무 달력 (C4). 설치 전체에서 공유한다 — 업무 시간은 회사의 성질이다. */
export interface BusinessCalendar {
  id: string
  name: string
  timezone: string
  /** `{"0": [["09:00","18:00"]], ...}` — 요일(월=0) → 구간. */
  working_hours: Record<string, [string, string][]>
  /** 통째로 쉬는 **현지 날짜**들 (`YYYY-MM-DD`). */
  holidays: string[]
}

export interface NewBusinessCalendar {
  name: string
  timezone: string
  working_hours: Record<string, [string, string][]>
  holidays?: string[]
}

export interface BusinessCalendarPatch {
  name?: string
  timezone?: string
  working_hours?: Record<string, [string, string][]>
  holidays?: string[]
}

/** SLA 정책의 목표 하나. 위에서부터 처음 맞는 것이 이긴다. */
export interface SlaGoal {
  seconds: number
  priority_min?: number
  request_type_id?: string
}

/**
 * "목표의 N% 를 썼을 때 이것을 한다" (C5).
 *
 * **초가 아니라 %다.** 목표 시간은 우선순위·요청 유형마다 다르므로 "3시간
 * 남았을 때" 는 4시간 목표에서는 45분 만에, 3일 목표에서는 거의 끝에 걸린다.
 *
 * 100 이 목표 시각이다. 그보다 크면 위반 뒤의 조치다.
 */
export interface SlaEscalation {
  at_percent: number
  action: 'notify' | 'raise_priority'
  /** `notify` 의 대상. */
  user_id?: string
  /** `raise_priority` 가 올릴 값 (1~5). */
  priority?: number
}

export interface SlaPolicy {
  id: string
  project_id: string
  name: string
  metric: string
  calendar_id: string
  /** 목록이 "무엇으로 재는지" 를 바로 보여 준다. */
  calendar_name: string
  goals: SlaGoal[]
  pause_state_ids: string[]
  escalations: SlaEscalation[]
  /**
   * 규칙이 지목한 사람의 이름. id → 이름.
   *
   * 규칙 **안이 아니라 옆에** 온다: 저장 요청은 읽은 규칙을 그대로 되돌려
   * 보내는데, 그 안에 이름이 섞이면 서버가 모르는 항목으로 거절한다.
   */
  escalation_user_names: Record<string, string>
  is_enabled: boolean
}

/**
 * 멈춤 상태로 고를 수 있는 상태 하나.
 *
 * **UUID 를 손으로 적게 하지 않는다.** 서버가 모르는 상태를 거절하므로,
 * 화면에는 고를 수 있는 것만 보여야 한다 — 거절만 하고 무엇을 고를 수
 * 있는지 안 알려 주면 관리자는 막힌다.
 */
export interface WorkflowStateOption {
  id: string
  name: string
  category: string
  /** 같은 이름의 상태가 워크플로우마다 따로 있다. 어느 쪽인지 알아야 한다. */
  workflow_name: string
}

export interface NewSlaPolicy {
  project_id: string
  name: string
  metric: string
  calendar_id: string
  goals: SlaGoal[]
  pause_state_ids?: string[]
  escalations?: SlaEscalation[]
}

/**
 * **`metric` 이 없다.** 응답 정책을 해결 정책으로 바꾸면 이미 걸린 클럭들이
 * 갑자기 다른 것을 재는 시계가 된다 — 지난 지표가 뜻을 잃는다.
 */
export interface SlaPolicyPatch {
  name?: string
  calendar_id?: string
  goals?: SlaGoal[]
  pause_state_ids?: string[]
  escalations?: SlaEscalation[]
  is_enabled?: boolean
}

/** IMAP 접속 설정. **비밀번호가 없다** — 따로 보내고 되돌려 받지 않는다. */
export interface EmailInbound {
  host: string
  user: string
  port: number
  folder: string
  /** 평문 IMAP. 기본은 아니다 — 비밀번호가 그대로 나간다. */
  use_ssl: boolean
}

export interface EmailChannel {
  id: string
  project_id: string
  address: string
  outbound_from: string
  inbound: EmailInbound
  /**
   * 비밀번호가 설정돼 있는가. **값 자체는 오지 않는다** — 되돌려주면 그것이
   * 브라우저의 메모리·로그·오류 보고를 거쳐 다니게 된다.
   */
  has_password: boolean
  default_request_type_id: string
  /** id 만 주면 화면이 요청 유형 목록을 또 받아 짜맞춰야 한다. */
  request_type_name: string
  is_enabled: boolean
  /**
   * 마지막 폴링에서 무엇이 잘못됐는가.
   *
   * **화면에 보여 준다.** 조용히 아무 메일도 안 들어오면 관리자는 "고객이
   * 안 보냈나" 로 읽는다.
   */
  last_error: string | null
  last_polled_at: string | null
}

export interface NewEmailChannel {
  project_id: string
  address: string
  outbound_from: string
  inbound: EmailInbound
  password: string
  default_request_type_id: string
}

/** **`password` 를 안 보내면 그대로 둔다.** 폼은 그 칸을 비워 두고 저장한다. */
export interface EmailChannelPatch {
  address?: string
  outbound_from?: string
  inbound?: EmailInbound
  password?: string
  default_request_type_id?: string
  is_enabled?: boolean
}

/** 요청 유형에 걸 수 있는 스페이스 (C8). `kind = "kb"` 만 온다. */
export interface KbSpace {
  id: string
  key: string
  name: string
}

/** 고객에게 추천하는 문서 한 편 (C8). 본문이 아니라 발췌만 온다. */
export interface Article {
  page_id: string
  /** `SPACE/slug`. 화면이 링크를 만든다. */
  ref: string
  title: string
  excerpt: string
}

// ── 자동화 규칙 (C9) ───────────────────────────────────────────

/** 규칙을 깨우는 이벤트. */
export type AutomationTrigger =
  | 'desk.ticket.submitted'
  | 'issue.transitioned'
  | 'issue.commented'

/** 조건에서 볼 수 있는 것. 서버의 `automation.FIELDS` 와 같아야 한다. */
export type AutomationField =
  | 'priority'
  | 'channel'
  | 'request_type_id'
  | 'organization_id'
  | 'state_category'
  | 'summary'
  | 'is_internal'
  | 'to_state_category'

export type AutomationOperator = 'eq' | 'ne' | 'gte' | 'lte' | 'in' | 'contains'

/** 조건 하나. **전부 맞아야 한다(AND).** */
export interface AutomationCondition {
  field: AutomationField
  op: AutomationOperator
  value: string | number | boolean | (string | number)[]
}

/** 조건·조치가 고를 수 있는 것 하나. */
export interface AutomationTargetOption {
  id: string
  name: string
}

/**
 * 고를 수 있는 것들. **자동화 권한으로 연다.**
 *
 * 요청 유형 목록은 `desk.portal.manage` 가, 고객 조직 목록은
 * `desk.customer.manage` 가 지킨다. 규칙을 쓰는 사람에게 그 둘을 마저
 * 요구하면 고를 수는 없는데 UUID 를 적으면 되는 자리가 된다.
 */
export interface AutomationTargets {
  request_types: AutomationTargetOption[]
  organizations: AutomationTargetOption[]
}

/** 조치 하나. 등록된 이름 + 파라미터만. */
export interface AutomationAction {
  kind: 'set_priority' | 'assign' | 'reply_with_canned' | 'add_note'
  priority?: number
  user_id?: string
  canned_response_id?: string
}

export interface AutomationRule {
  id: string
  project_id: string
  name: string
  trigger: { event: AutomationTrigger }
  conditions: AutomationCondition[]
  actions: AutomationAction[]
  /**
   * 조치가 지목한 사람·정형 응답의 이름. id → 이름.
   *
   * 조치 **안이 아니라 옆에** 온다: 저장 요청은 읽은 조치를 그대로 되돌려
   * 보내는데, 그 안에 이름이 섞이면 서버가 모르는 항목으로 거절한다.
   */
  names: Record<string, string>
  position: number
  is_enabled: boolean
}

export interface NewAutomationRule {
  project_id: string
  name: string
  trigger: { event: AutomationTrigger }
  conditions?: AutomationCondition[]
  actions: AutomationAction[]
  position?: number
}

export interface AutomationRulePatch {
  name?: string
  trigger?: { event: AutomationTrigger }
  conditions?: AutomationCondition[]
  actions?: AutomationAction[]
  position?: number
  is_enabled?: boolean
}

const PORTALS = '/api/v1/portals'
const CUSTOMERS = '/api/v1/customer-organizations'
const QUEUES = '/api/v1/queues'
const CANNED = '/api/v1/canned-responses'
const CALENDARS = '/api/v1/business-calendars'
const SLA = '/api/v1/sla-policies'
const EMAIL = '/api/v1/email-channels'
const AUTOMATION = '/api/v1/automation-rules'
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
    listKbSpaces: () => client.get<KbSpace[]>(`${PORTALS}/kb-spaces`),

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
    /**
     * 요청 중 문서 추천 (C8). **게스트도 부른다** — 익명 요청이다.
     *
     * 서버가 내주는 것은 제한 없는 공개 문서뿐이다. 빈 질의에는 빈 목록이
     * 온다: 아무 것도 안 적었는데 목록이 뜨면 추천이 아니라 스페이스 공개다.
     */
    portalArticles: (slug: string, requestTypeId: string, q: string) =>
      client.get<Article[]>(
        `${PORTAL}/${slug}/request-types/${requestTypeId}/articles?q=${encodeURIComponent(q)}`,
        { anonymous: true },
      ),

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

    // ── SLA (C4) ────────────────────────────────────────────
    // step-up 이 필요하다. 화면은 `auth.mfa_required` 를 받으면 2FA 를
    // 물어야 한다 — "권한 없음" 과 다른 상황이다.
    listCalendars: () => client.get<BusinessCalendar[]>(CALENDARS),
    createCalendar: (body: NewBusinessCalendar) =>
      client.post<BusinessCalendar>(CALENDARS, body),
    updateCalendar: (id: string, body: BusinessCalendarPatch) =>
      client.patch<BusinessCalendar>(`${CALENDARS}/${id}`, body),
    deleteCalendar: (id: string) => client.delete<void>(`${CALENDARS}/${id}`),

    listSlaPolicies: (projectId: string) =>
      client.get<SlaPolicy[]>(`${SLA}?project_id=${encodeURIComponent(projectId)}`),
    createSlaPolicy: (body: NewSlaPolicy) => client.post<SlaPolicy>(SLA, body),
    updateSlaPolicy: (id: string, body: SlaPolicyPatch) =>
      client.patch<SlaPolicy>(`${SLA}/${id}`, body),
    deleteSlaPolicy: (id: string) => client.delete<void>(`${SLA}/${id}`),
    listPauseStateOptions: (projectId: string) =>
      client.get<WorkflowStateOption[]>(`${SLA}/states?project_id=${projectId}`),

    // 메일 채널 (C6)
    listEmailChannels: (projectId: string) =>
      client.get<EmailChannel[]>(`${EMAIL}?project_id=${projectId}`),
    createEmailChannel: (body: NewEmailChannel) => client.post<EmailChannel>(EMAIL, body),
    updateEmailChannel: (id: string, body: EmailChannelPatch) =>
      client.patch<EmailChannel>(`${EMAIL}/${id}`, body),
    deleteEmailChannel: (id: string) => client.delete<void>(`${EMAIL}/${id}`),

    // 자동화 규칙 (C9)
    listAutomationRules: (projectId: string) =>
      client.get<AutomationRule[]>(`${AUTOMATION}?project_id=${projectId}`),
    /** 조건·조치가 고를 수 있는 것들. UUID 를 손으로 적게 하지 않는다. */
    automationTargets: (projectId: string) =>
      client.get<AutomationTargets>(`${AUTOMATION}/targets?project_id=${projectId}`),
    createAutomationRule: (body: NewAutomationRule) =>
      client.post<AutomationRule>(AUTOMATION, body),
    updateAutomationRule: (id: string, body: AutomationRulePatch) =>
      client.patch<AutomationRule>(`${AUTOMATION}/${id}`, body),
    deleteAutomationRule: (id: string) => client.delete<void>(`${AUTOMATION}/${id}`),
  }
}

export type DeskApi = ReturnType<typeof createDeskApi>
