import type { ApiClient } from './client'

export interface BoardColumnSpec {
  name: string
  iql: string
  wip_limit: number | null
}

export interface Board {
  id: string
  project_id: string
  name: string
  columns: BoardColumnSpec[]
  swimlane_by: string | null
  base_iql: string | null
  position: number
}

export interface BoardCard {
  id: string
  key: string
  summary: string
  type_id: string
  type_name: string
  state_id: string
  state_name: string
  state_category: string
  assignee_id: string | null
  priority: number
  due_date: string | null
  labels: string[]
  version: number
}

export interface BoardColumnContent {
  name: string
  iql: string
  wip_limit: number | null
  issues: BoardCard[]
  loaded: number
  /** 페이지 크기에서 잘렸다. UI 는 "50+" 로 표시한다. */
  truncated: boolean
  over_wip: boolean
}

export interface BoardContent {
  board: Board
  columns: BoardColumnContent[]
}

export interface NewBoard {
  project_id: string
  name: string
  columns: { name: string; iql: string; wip_limit?: number | null }[]
  swimlane_by?: string | null
  base_iql?: string | null
}

export interface BoardPatch {
  name?: string
  columns?: { name: string; iql: string; wip_limit?: number | null }[]
  swimlane_by?: string | null
  base_iql?: string | null
  clear_swimlane?: boolean
  clear_base_iql?: boolean
}

const BASE = '/api/v1/boards'

export function createBoardsApi(client: ApiClient) {
  return {
    list: (projectId: string) => client.get<Board[]>(`${BASE}?project_id=${projectId}`),
    get: (id: string) => client.get<Board>(`${BASE}/${id}`),
    content: (id: string) => client.get<BoardContent>(`${BASE}/${id}/content`),
    create: (body: NewBoard) => client.post<Board>(BASE, body),
    update: (id: string, body: BoardPatch) => client.patch<Board>(`${BASE}/${id}`, body),
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),

    /** 드래그 전이. version 을 주면 낙관적 잠금이 걸린다. */
    move: (
      id: string,
      body: { issue_id: string; transition_id: string },
      version?: number,
    ) =>
      client.post<BoardCard>(
        `${BASE}/${id}/move`,
        body,
        version === undefined ? undefined : { headers: { 'If-Match': String(version) } },
      ),
  }
}

export type BoardsApi = ReturnType<typeof createBoardsApi>
