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

export interface IqlValidation {
  valid: boolean
  /** offset/length 로 에디터가 밑줄을 긋는다. */
  error: { code: string; message: string; offset?: number; length?: number } | null
  fields: string[] | null
}

export interface SavedFilter {
  id: string
  owner_id: string
  name: string
  description: string | null
  iql: string
  is_shared: boolean
}

export function createSearchApi(client: ApiClient) {
  return {
    search: (body: { iql: string; cursor?: string; limit?: number }) =>
      client.post<IssuePage>('/api/v1/search/issues', body),

    validate: (iql: string) => client.post<IqlValidation>('/api/v1/iql/validate', { iql }),

    catalog: () => client.get<IqlCatalog>('/api/v1/iql/fields'),

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
