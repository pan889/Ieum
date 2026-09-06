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
    list: (params: { cursor?: string; limit?: number; includeArchived?: boolean } = {}) => {
      const query = new URLSearchParams()
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.includeArchived) query.set('include_archived', 'true')
      const suffix = query.size > 0 ? `?${query}` : ''
      return client.get<ProjectPage>(`${BASE}${suffix}`)
    },
    get: (id: string) => client.get<Project>(`${BASE}/${id}`),
    create: (body: NewProject) => client.post<Project>(BASE, body),
    archive: (id: string) => client.post<Project>(`${BASE}/${id}/archive`),
  }
}

export type ProjectsApi = ReturnType<typeof createProjectsApi>
