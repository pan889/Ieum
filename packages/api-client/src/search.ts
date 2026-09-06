import type { ApiClient } from './client'
import type { IssuePage } from './issues'

export interface IqlFieldSpec {
  name: string
  type: string
  description: string
  operators: string[]
  sortable: boolean
  nullable: boolean
}

export interface IqlFunctionSpec {
  name: string
  returns: string
  description: string
  min_args: number
  max_args: number
}

export interface IqlCatalog {
  fields: IqlFieldSpec[]
  functions: IqlFunctionSpec[]
}

export interface IqlError {
  code: string
  /** 서버 문구(한국어 고정, 디버깅용). 화면은 code 로 번역한다. */
  message: string
  /** 틀린 자리. 서버는 이걸 반드시 준다 (query-language.md 4절). */
  offset?: number
  length?: number
  /** 오타에 가까운 이름. 있으면 한 번 눌러 고칠 수 있다. */
  suggestions?: string[]
  /** ICU 변수로 넘길 나머지(field, operator, milestone …). */
  [key: string]: unknown
}

export interface IqlValidation {
  valid: boolean
  error: IqlError | null
  fields: string[] | null
}

export type SuggestKind = 'field' | 'operator' | 'function' | 'value' | 'keyword'

export interface IqlSuggestion {
  /** 목록에 보이는 글자. */
  label: string
  /** 실제로 끼워 넣을 글자. 사용자 값은 label(이름)과 다르다(ID). */
  insert: string
  kind: SuggestKind
  detail: string
  /** insert 안에서 커서가 멈출 자리. 함수는 괄호 안이다. */
  caret: number
}

export interface IqlSuggestions {
  /** 갈아 끼울 범위. 닫는 따옴표를 삼키느라 커서보다 뒤로 갈 수 있다. */
  offset: number
  length: number
  items: IqlSuggestion[]
}

export interface SavedFilter {
  id: string
  owner_id: string
  name: string
  description: string | null
  iql: string
  is_shared: boolean
}


export type SearchKind = 'issue' | 'page'

export interface SearchHit {
  kind: SearchKind
  entity_id: string
  /** 사람이 읽는 식별자. 이슈 키(`ENG-1`)나 문서 경로(`ENG/deploy`). */
  ref: string
  title: string
  snippet: string
  scope_id: string
  updated_at: string
}

export interface UnifiedSearchPage {
  items: SearchHit[]
  total: number
  /** 화면이 굵게 칠할 낱말. 서버는 HTML 을 만들지 않는다. */
  keywords: string[]
}

export function createSearchApi(client: ApiClient) {
  return {
    search: (body: { iql: string; cursor?: string; limit?: number }) =>
      client.post<IssuePage>('/api/v1/search/issues', body),

    /** 이슈와 문서를 한 번에. 권한은 서버가 질의에 얹어 거른다. */
    everything: (params: { q: string; kind?: SearchKind[]; limit?: number; offset?: number }) => {
      const query = new URLSearchParams({ q: params.q })
      for (const kind of params.kind ?? []) query.append('kind', kind)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.offset) query.set('offset', String(params.offset))
      return client.get<UnifiedSearchPage>(`/api/v1/search?${query.toString()}`)
    },

    validate: (iql: string) => client.post<IqlValidation>('/api/v1/iql/validate', { iql }),

    catalog: () => client.get<IqlCatalog>('/api/v1/iql/fields'),

    /** 커서 위치 기반 자동완성. 깨진 질의에는 빈 목록이 온다 — 오류는 validate 가 말한다. */
    suggest: (body: { iql: string; offset: number; limit?: number }) =>
      client.post<IqlSuggestions>('/api/v1/iql/suggest', body),

    /**
     * IQL 결과를 CSV 로. 스트리밍 응답이라 blob 으로 받는다.
     *
     * ApiClient 는 JSON 을 전제로 하므로 여기서만 fetch 를 직접 쓴다.
     */
    exportCsv: (iql: string) => client.postForBlob('/api/v1/search/issues/export', { iql }),

    filters: {
      list: () => client.get<SavedFilter[]>('/api/v1/filters'),
      create: (body: { name: string; iql: string; description?: string; is_shared?: boolean }) =>
        client.post<SavedFilter>('/api/v1/filters', body),
      remove: (id: string) => client.delete<void>(`/api/v1/filters/${id}`),
      run: (id: string, params: { cursor?: string; limit?: number } = {}) => {
        const query = new URLSearchParams()
        if (params.cursor) query.set('cursor', params.cursor)
        if (params.limit) query.set('limit', String(params.limit))
        const suffix = query.size > 0 ? `?${query.toString()}` : ''
        return client.get<IssuePage>(`/api/v1/filters/${id}/results${suffix}`)
      },
    },
  }
}

export type SearchApi = ReturnType<typeof createSearchApi>
