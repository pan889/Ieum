import type { ApiClient } from './client'

export interface Space {
  id: string
  key: string
  name: string
  description: string | null
  kind: string
  home_page_id: string | null
  archived_at: string | null
}

export interface SpacePage {
  items: Space[]
  next_cursor: string | null
  total: number | null
}

export interface WikiPage {
  id: string
  space_id: string
  space_key: string
  parent_id: string | null
  path: string
  slug: string
  title: string
  status: string
  position: number
  version: number
  labels: string[]
  body: string
  front_matter: Record<string, unknown>
  /** 지금 보고 있는 판. 초안만 있으면 null. */
  version_number: number | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

/** 트리의 한 칸. 본문은 없다 — 트리에 본문이 실리면 무거워진다. */
export interface PageNode {
  id: string
  parent_id: string | null
  path: string
  slug: string
  title: string
  status: string
  position: number
}

export interface PageVersionSummary {
  id: string
  number: number
  title: string
  author_id: string | null
  message: string | null
  created_at: string
}

export interface PageVersionDetail extends PageVersionSummary {
  body: string
  front_matter: Record<string, unknown>
}

export interface NewSpace {
  key: string
  name: string
  description?: string
  kind?: 'team' | 'personal' | 'kb'
}

export interface NewWikiPage {
  space_id: string
  title: string
  parent_id?: string | null
  body?: string
  front_matter?: Record<string, unknown>
  labels?: string[]
  publish?: boolean
}

export interface WikiPagePatch {
  title?: string
  body?: string
  front_matter?: Record<string, unknown>
  labels?: string[]
  /** 변경 요약. 커밋 메시지에 해당한다. */
  message?: string
  publish?: boolean
}

export interface PageRestriction {
  mode: string
  principal_kind: string
  principal_id: string
}

const SPACES = '/api/v1/spaces'
const PAGES = '/api/v1/pages'

function ifMatch(version?: number): Record<string, string> | undefined {
  return version === undefined ? undefined : { 'If-Match': String(version) }
}

export function createWikiApi(client: ApiClient) {
  return {
    spaces: {
      list: (params: { cursor?: string; limit?: number; q?: string } = {}) => {
        const query = new URLSearchParams()
        if (params.cursor) query.set('cursor', params.cursor)
        if (params.limit) query.set('limit', String(params.limit))
        if (params.q) query.set('q', params.q)
        const suffix = query.size > 0 ? `?${query.toString()}` : ''
        return client.get<SpacePage>(`${SPACES}${suffix}`)
      },
      get: (id: string) => client.get<Space>(`${SPACES}/${id}`),
      getByKey: (key: string) => client.get<Space>(`${SPACES}/by-key/${key}`),
      create: (body: NewSpace) => client.post<Space>(SPACES, body),
      update: (id: string, body: Partial<NewSpace> & { home_page_id?: string | null; clear_home_page?: boolean }) =>
        client.patch<Space>(`${SPACES}/${id}`, body),
      archive: (id: string) => client.post<Space>(`${SPACES}/${id}/archive`),
      tree: (id: string) => client.get<PageNode[]>(`${SPACES}/${id}/tree`),

      /** `.md` 하나 또는 `.md` 를 담은 ZIP. 서버가 내용을 읽어야 해서
       *  스토리지를 거치지 않는다(첨부와 다르다). */
      importFile: (id: string, file: File, parentId?: string) => {
        const suffix = parentId ? `?parent_id=${parentId}` : ''
        return client.postFile<WikiPage[]>(`${SPACES}/${id}/import${suffix}`, file)
      },
      exportZip: (id: string) => client.getBlob(`${SPACES}/${id}/export`),
    },

    pages: {
      get: (id: string) => client.get<WikiPage>(`${PAGES}/${id}`),
      /** `SPACE` + `부모/자식` 으로 연다. 사람이 주고받는 주소다. */
      getByPath: (space: string, path: string) =>
        client.get<WikiPage>(
          `${PAGES}/by-path?space=${encodeURIComponent(space)}&path=${encodeURIComponent(path)}`,
        ),
      create: (body: NewWikiPage) => client.post<WikiPage>(PAGES, body),
      update: (id: string, body: WikiPagePatch, version?: number) =>
        client.patch<WikiPage>(`${PAGES}/${id}`, body, ifMatch(version)),
      move: (id: string, body: { new_parent_id: string | null; position?: number }) =>
        client.post<WikiPage>(`${PAGES}/${id}/move`, body),
      archive: (id: string) => client.post<WikiPage>(`${PAGES}/${id}/archive`),

      versions: (id: string) => client.get<PageVersionSummary[]>(`${PAGES}/${id}/versions`),
      version: (id: string, number: number) =>
        client.get<PageVersionDetail>(`${PAGES}/${id}/versions/${String(number)}`),
      restore: (id: string, number: number) =>
        client.post<WikiPage>(`${PAGES}/${id}/versions/${String(number)}/restore`),

      exportMarkdown: (id: string) => client.getBlob(`${PAGES}/${id}/export`),

      restrictions: (id: string) => client.get<PageRestriction[]>(`${PAGES}/${id}/restrictions`),
      setRestrictions: (
        id: string,
        body: { mode: 'view' | 'edit'; principals: { kind: 'user' | 'group'; id: string }[] },
      ) => client.put<PageRestriction[]>(`${PAGES}/${id}/restrictions`, body),
    },
  }
}

export type WikiApi = ReturnType<typeof createWikiApi>
