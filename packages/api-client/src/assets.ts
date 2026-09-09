/**
 * 자산·구성 항목과 티켓의 연결 (C15, M6).
 *
 * **전체 목록을 받는 함수가 없다.** 자산은 수천 개가 되고, 목록을 통째로 받아
 * 드롭다운에 넣으면 마지막에 만든 것을 고를 수 없다 — 이 저장소가 세 번
 * 겪은 일이다. 여기서 주는 것은 커서 페이지뿐이다.
 */

import type { ApiClient } from './client'

export type AssetStatus = 'in_use' | 'spare' | 'repair' | 'retired'

export interface AssetType {
  id: string
  name: string
  icon: string | null
  position: number
  is_archived: boolean
}

/**
 * 자산에 붙일 고객 조직 후보. 이름과 id 뿐이다.
 *
 * 조직 관리 목록(`deskApi.listOrgs`)을 쓰지 않는 이유는 **step-up** 이다:
 * 그 손잡이는 2FA 를 다시 요구하고, 자산 등록은 일부러 아니다. 관리 목록을
 * 자산 화면에서 쓰면 화면을 여는 것만으로 403 이 난다.
 */
export interface OrgChoice {
  id: string
  name: string
}

export interface OrgChoicePage {
  items: OrgChoice[]
  /** 상한에 닿았는가. **잘렸으면 잘렸다고 말해야 한다** (ux-principles). */
  has_more: boolean
}

export interface Asset {
  id: string
  type_id: string
  type_name: string
  name: string
  /** 자산번호. 대문자로 저장된다. 없는 자산이 흔하다(교실, 서비스). */
  tag: string | null
  status: AssetStatus
  owner_id: string | null
  owner_name: string | null
  organization_id: string | null
  organization_name: string | null
  location: string | null
  note: string | null
  /** 이 자산에 걸린 티켓 수. 자꾸 고장나는 장비를 찾는 근거다. */
  ticket_count: number
  created_at: string
}

export interface AssetPage {
  items: Asset[]
  next_cursor: string | null
}

/** 티켓 화면이 쓰는 만큼만. 메모와 담당자는 자산 화면의 것이다. */
export interface LinkedAsset {
  id: string
  type_name: string
  name: string
  tag: string | null
  status: AssetStatus
  location: string | null
  organization_name: string | null
}

export interface AssetTicket {
  issue_id: string
  key: string
  summary: string
  state_name: string
  state_category: string
}

export interface NewAsset {
  type_id: string
  name: string
  tag?: string | null
  status?: AssetStatus
  owner_id?: string | null
  organization_id?: string | null
  location?: string | null
  note?: string | null
}

export interface AssetPatch {
  name?: string
  tag?: string
  /** 자산번호를 **비운다.** `tag: undefined` 는 "안 건드린다" 다. */
  clear_tag?: boolean
  status?: AssetStatus
  owner_id?: string
  clear_owner?: boolean
  organization_id?: string
  clear_organization?: boolean
  location?: string
  note?: string
}

export interface AssetQuery {
  q?: string
  type_id?: string
  status?: AssetStatus
  organization_id?: string
  /** 기본은 꺼져 있다 — 나간 장비가 후보에 섞이면 잘못 고른다. */
  include_retired?: boolean
  limit?: number
  cursor?: string
}

const BASE = '/api/v1/assets'

function search(params: AssetQuery): string {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  if (params.type_id) query.set('type_id', params.type_id)
  if (params.status) query.set('status', params.status)
  if (params.organization_id) query.set('organization_id', params.organization_id)
  if (params.include_retired) query.set('include_retired', 'true')
  if (params.limit) query.set('limit', String(params.limit))
  if (params.cursor) query.set('cursor', params.cursor)
  const text = query.toString()
  return text ? `?${text}` : ''
}

export function createAssetsApi(client: ApiClient) {
  return {
    types: (includeArchived = false) =>
      client.get<AssetType[]>(`${BASE}/types${includeArchived ? '?include_archived=true' : ''}`),
    createType: (body: { name: string; icon?: string | null; position?: number }) =>
      client.post<AssetType>(`${BASE}/types`, body),
    setTypeArchived: (id: string, isArchived: boolean) =>
      client.post<AssetType>(`${BASE}/types/${id}/archived`, { is_archived: isArchived }),

    /** 이름·자산번호·위치로 찾는다. 커서 페이지다. */
    search: (params: AssetQuery = {}) => client.get<AssetPage>(`${BASE}${search(params)}`),
    /** 자산에 붙일 고객 조직을 찾는다. **검색이다** — 조직은 수백 개가 된다. */
    organizations: (params: { q?: string; limit?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.limit) query.set('limit', String(params.limit))
      const text = query.toString()
      return client.get<OrgChoicePage>(`${BASE}/organizations${text ? `?${text}` : ''}`)
    },
    get: (id: string) => client.get<Asset>(`${BASE}/${id}`),
    create: (body: NewAsset) => client.post<Asset>(BASE, body),
    update: (id: string, body: AssetPatch) => client.patch<Asset>(`${BASE}/${id}`, body),
    /** 지운다. **티켓에 이어져 있으면 409** — 그때는 '사용 종료' 로 둔다. */
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),
    /** 이 자산에 걸린 티켓. 최신순. */
    tickets: (id: string) => client.get<AssetTicket[]>(`${BASE}/${id}/tickets`),

    linked: (issueId: string) =>
      client.get<LinkedAsset[]>(`${BASE}/linked?issue_id=${encodeURIComponent(issueId)}`),
    link: (issueId: string, assetId: string) =>
      client.post<LinkedAsset>(`${BASE}/linked?issue_id=${encodeURIComponent(issueId)}`, {
        asset_id: assetId,
      }),
    unlink: (issueId: string, assetId: string) =>
      client.delete<void>(
        `${BASE}/linked?issue_id=${encodeURIComponent(issueId)}` +
          `&asset_id=${encodeURIComponent(assetId)}`,
      ),
  }
}

export type AssetsApi = ReturnType<typeof createAssetsApi>
