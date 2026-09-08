import type { ApiClient } from './client'

/** 보드가 스프린트를 다루는 방식. */
export type SprintMode = 'all' | 'active'

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
  /** 스프린트를 아는가. `'active'` 면 도는 스프린트의 이슈만 보여 준다. */
  sprint_mode: SprintMode
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

/** 보드의 가로 줄. 스윔레인이 없으면 `key` 가 빈 레인 하나만 온다. */
export interface BoardSwimlane {
  key: string
  /** priority 는 키("1"~"5")가 그대로 온다 — 번역은 화면이 한다. */
  label: string
  columns: BoardColumnContent[]
}

/** 보드가 **실제로 걸어 준** 스프린트. */
export interface BoardSprintRef {
  id: string
  name: string
}

export interface BoardContent {
  board: Board
  lanes: BoardSwimlane[]
  /**
   * `board.sprint_mode` 가 `'active'` 인데 여기가 `null` 이면 도는 스프린트가
   * 없어서 **거르지 않은** 것이다. 화면은 그 사실을 말해야 한다 — 안 그러면
   * 백로그까지 올라온 보드를 "이번 스프린트" 로 읽는다.
   */
  sprint: BoardSprintRef | null
}

/** 서버가 나눌 수 있는 기준. 다른 값은 요청 단계에서 거절된다. */
export const SWIMLANE_FIELDS = ['assignee', 'priority', 'type'] as const
export type SwimlaneField = (typeof SWIMLANE_FIELDS)[number]

export interface NewBoard {
  project_id: string
  name: string
  columns: { name: string; iql: string; wip_limit?: number | null }[]
  swimlane_by?: SwimlaneField | null
  base_iql?: string | null
  sprint_mode?: SprintMode
}

export interface BoardPatch {
  name?: string
  columns?: { name: string; iql: string; wip_limit?: number | null }[]
  swimlane_by?: SwimlaneField | null
  base_iql?: string | null
  sprint_mode?: SprintMode
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
