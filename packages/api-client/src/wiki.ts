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
  /** `page`(트리) 또는 `blog`(날짜순). */
  kind: string
  /** 블로그 글이 흐르는 기준 시각. 트리 문서에는 없다. */
  published_at: string | null
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
  /** `blog` 면 트리가 아니라 날짜순 목록에 들어간다. */
  kind?: 'page' | 'blog'
}

export interface BlogPage {
  items: WikiPage[]
  total: number
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


/**
 * 인용으로 위치를 잡는 앵커 (wiki-markdown.md 6절).
 *
 * **평문 기준**이다. 사람은 렌더된 글을 드래그하지 `**굵게**` 같은 원문을
 * 고르지 않는다.
 */
export interface CommentAnchor {
  exact: string
  prefix: string
  suffix: string
  /** 같은 인용이 여러 번 나올 때 몇 번째인지. 1부터. */
  occurrence: number
  version_number: number | null
}

/** 지금 문서에서 인용이 붙은 자리. 못 붙었으면 코멘트의 match 가 null. */
export interface CommentMatch {
  start: number
  end: number
  how: 'exact' | 'fuzzy'
  score: number
  /** 지금 문서에 실제로 있는 글자. 퍼지로 붙었으면 인용문과 다르다. */
  found: string
}

export interface PageComment {
  id: string
  page_id: string
  author_id: string | null
  body: string
  parent_id: string | null
  anchor: CommentAnchor | null
  /** 못 붙었으면 `orphaned`. 인용문은 남는다 — 조용히 지우지 않는다. */
  anchor_status: 'ok' | 'orphaned'
  match: CommentMatch | null
  resolved_at: string | null
  edited_at: string | null
  created_at: string
}

export interface NewPageComment {
  body: string
  /** 없으면 문서 전체에 다는 코멘트다. */
  anchor?: Omit<CommentAnchor, 'version_number'> & { version_number?: number | null }
  parent_id?: string | null
}


export interface LinkedPage {
  id: string
  space_id: string
  space_key: string
  path: string
  title: string
}

export interface PageTemplate {
  id: string
  /** null 이면 전역 템플릿. */
  space_id: string | null
  name: string
  body: string
  category: string | null
}

export interface NewPageTemplate {
  name: string
  body?: string
  category?: string | null
}


export interface DiffLine {
  op: 'equal' | 'insert' | 'delete'
  /** 이전 판의 줄 번호. 추가된 줄이면 null. */
  old_number: number | null
  /** 이후 판의 줄 번호. 지워진 줄이면 null. */
  new_number: number | null
  text: string
}

export interface VersionDiff {
  page_id: string
  before: number
  after: number
  before_created_at: string
  after_created_at: string
  lines: DiffLine[]
  added: number
  removed: number
  /** 상한에 걸려 잘렸으면 true. */
  truncated: boolean
}


export interface PageDraft {
  page_id: string
  title: string
  body: string
  /** 초안을 뜨기 시작한 판. 그 사이 남이 고쳤는지 화면이 판단한다. */
  base_version: number | null
  updated_at: string
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

      /** 블로그 글, 최신순. 트리와 달리 시간이 자리를 정한다. */
      blog: (id: string, params: { limit?: number; offset?: number } = {}) => {
        const query = new URLSearchParams()
        if (params.limit) query.set('limit', String(params.limit))
        if (params.offset) query.set('offset', String(params.offset))
        const suffix = query.size > 0 ? `?${query.toString()}` : ''
        return client.get<BlogPage>(`${SPACES}/${id}/blog${suffix}`)
      },

      templates: {
        /** 그 스페이스 것 + 전역. */
        list: (id: string) => client.get<PageTemplate[]>(`${SPACES}/${id}/templates`),
        create: (id: string, body: NewPageTemplate) =>
          client.post<PageTemplate>(`${SPACES}/${id}/templates`, body),
        remove: (templateId: string) =>
          client.delete<void>(`${SPACES}/templates/${templateId}`),
      },
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
      /** 가지째 복사. 이력은 따라가지 않는다 — 복사본은 새 문서다. */
      copy: (id: string, body: { new_parent_id?: string | null; title?: string } = {}) =>
        client.post<WikiPage>(`${PAGES}/${id}/copy`, body),
      archive: (id: string) => client.post<WikiPage>(`${PAGES}/${id}/archive`),

      draft: {
        get: (id: string) => client.get<PageDraft | null>(`${PAGES}/${id}/draft`),
        save: (
          id: string,
          body: { title: string; body: string; base_version?: number | null },
        ) => client.put<PageDraft>(`${PAGES}/${id}/draft`, body),
        discard: (id: string) => client.delete<void>(`${PAGES}/${id}/draft`),
      },

      versions: (id: string) => client.get<PageVersionSummary[]>(`${PAGES}/${id}/versions`),
      version: (id: string, number: number) =>
        client.get<PageVersionDetail>(`${PAGES}/${id}/versions/${String(number)}`),
      restore: (id: string, number: number) =>
        client.post<WikiPage>(`${PAGES}/${id}/versions/${String(number)}/restore`),
      diff: (id: string, before: number, after: number) =>
        client.get<VersionDiff>(
          `${PAGES}/${id}/diff?before=${String(before)}&after=${String(after)}`,
        ),

      exportMarkdown: (id: string) => client.getBlob(`${PAGES}/${id}/export`),

      /** 이 이슈를 본문에서 참조한 문서들. 위키가 답한다(모듈 경계). */
      mentioning: (issueId: string) =>
        client.get<LinkedPage[]>(`${PAGES}/mentioning/${issueId}`),

      comments: {
        list: (pageId: string) => client.get<PageComment[]>(`${PAGES}/${pageId}/comments`),
        add: (pageId: string, body: NewPageComment) =>
          client.post<PageComment>(`${PAGES}/${pageId}/comments`, body),
        edit: (commentId: string, body: string) =>
          client.patch<PageComment>(`${PAGES}/comments/${commentId}`, { body }),
        resolve: (commentId: string, resolved: boolean) =>
          client.post<PageComment>(`${PAGES}/comments/${commentId}/resolve`, { resolved }),
        remove: (commentId: string) => client.delete<void>(`${PAGES}/comments/${commentId}`),
      },

      /**
       * 동시 편집 세션에 붙을 표를 한 장 받는다 (B16).
       *
       * **표는 한 번만 쓴다.** 소켓이 끊겼다 붙을 때는 새로 받아야 한다 —
       * 액세스 토큰을 URL 에 싣지 않으려고 고른 모양이고, 재사용되면 URL 이
       * 새는 순간 남의 편집 세션에 붙을 수 있다.
       *
       * `url` 은 서버가 만들어 준다. 화면이 경로를 짜면 한쪽만 바뀌는 날이
       * 온다.
       */
      collabTicket: (id: string) =>
        client.post<CollabTicket>(`${PAGES}/${id}/collab-ticket`),

      restrictions: (id: string) => client.get<PageRestriction[]>(`${PAGES}/${id}/restrictions`),
      setRestrictions: (
        id: string,
        body: { mode: 'view' | 'edit'; principals: { kind: 'user' | 'group'; id: string }[] },
      ) => client.put<PageRestriction[]>(`${PAGES}/${id}/restrictions`, body),
    },
  }
}

export interface CollabTicket {
  /** 소켓 URL 에 실을 표. **한 번만 쓸 수 있다.** */
  ticket: string
  /** 붙을 곳(표까지 포함된 경로). 스킴만 `ws:`/`wss:` 로 바꿔 쓴다. */
  url: string
  /** 이 프로세스에서 지금 붙어 있는 사람 수. 설치 전체의 수가 아니다 —
   *  정확한 수는 붙은 뒤 프레즌스가 알려 준다. */
  editors: number
  /** 한 문서에 붙을 수 있는 상한. 거절 이유를 화면이 말할 수 있게. */
  max_editors: number
}

export type WikiApi = ReturnType<typeof createWikiApi>
