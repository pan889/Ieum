/**
 * 코드 저장소 연동 — 커밋·PR 을 이슈에 잇는다 (A22, M6).
 */

import type { ApiClient } from './client'

export type VcsProvider = 'github' | 'gitlab'

export interface Repository {
  id: string
  provider: VcsProvider
  name: string
  url: string | null
  project_ids: string[]
  is_enabled: boolean
  /**
   * 마지막으로 뭔가 받은 때. **켰는데 안 오는 것**이 이 연동의 흔한
   * 고장이고, 화면은 이 값이 비어 있는 것으로만 그것을 말할 수 있다.
   */
  last_event_at: string | null
}

export interface IssuedRepository extends Repository {
  /** **등록 응답에만 있다.** 다시 못 읽는다 — 잃으면 새로 만든다. */
  secret: string
  /** 코드 호스트에 넣을 주소. 화면이 경로를 짜지 않게 서버가 준다. */
  webhook_path: string
}

export interface RepositoryInput {
  provider: VcsProvider
  name: string
  /** 이 저장소의 커밋이 가리킬 수 있는 프로젝트. **비울 수 없다.** */
  project_ids: string[]
  url?: string | null
}

export interface ChangeLink {
  id: string
  kind: 'commit' | 'pull_request'
  /** 커밋이면 SHA, PR 이면 번호. */
  external_ref: string
  title: string
  url: string
  author: string | null
  /** `fixes ENG-12` 처럼 닫는다고 적혀 있었나. **상태는 옮기지 않는다.** */
  closing: boolean
  happened_at: string
  repository_name: string
  provider: VcsProvider
}

const BASE = '/api/v1/repositories'

export function createRepositoriesApi(client: ApiClient) {
  return {
    list: (projectId: string) =>
      client.get<Repository[]>(`${BASE}?project_id=${encodeURIComponent(projectId)}`),
    /** 등록한다. **시크릿은 이 응답에만 나온다.** */
    create: (body: RepositoryInput) => client.post<IssuedRepository>(BASE, body),
    setEnabled: (id: string, isEnabled: boolean) =>
      client.post<Repository>(`${BASE}/${id}/enabled`, { is_enabled: isEnabled }),
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),
    /** 이 이슈에 붙은 커밋·PR. 최신순. */
    links: (issueId: string) =>
      client.get<ChangeLink[]>(`${BASE}/links?issue_id=${encodeURIComponent(issueId)}`),
  }
}

export type RepositoriesApi = ReturnType<typeof createRepositoriesApi>
