/**
 * 앱 등록과 확장 지점 (M6 "플러그인 훅").
 *
 * **앱은 코드를 보내지 않는다. 글을 보내고 우리가 그린다.** 원격 스크립트나
 * iframe 을 받지 않는 이유는 서버의 `plugins/slots.py` 머리에 적어 두었다 —
 * 남의 자바스크립트를 우리 페이지에 넣으면 그쪽이 세션과 DOM 을 함께 갖는다.
 *
 * 그래서 이 클라이언트에는 "스크립트 주소" 같은 필드가 없다. 링크의 주소와
 * 패널의 마크다운뿐이다.
 */

import type { ApiClient } from './client'

/** 앱이 놓을 수 있는 것의 종류. 서버의 `slots.KINDS` 와 같은 어휘다. */
export type SlotKind = 'link' | 'panel'

export interface SlotCatalogEntry {
  name: string
  kind: SlotKind
  /** 관리 화면이 읽는 설명. 관리자용 개발 메모라 번역하지 않는다. */
  where: string
  placeholders: string[]
}

export interface AppSlot {
  id: string
  slot: string
  kind: SlotKind
  label: string
  url_template: string | null
  position: number
}

/** 이벤트를 받는 자리의 상태. 안 받는 앱은 `null` 이다. */
export interface AppStream {
  url: string
  events: string[]
  enabled: boolean
  consecutive_failures: number
  disabled_reason: string | null
}

export interface App {
  id: string
  name: string
  slug: string
  description: string | null
  homepage_url: string | null
  enabled: boolean
  /** 앞머리만. **원문은 발급 때 한 번 나가고 서버도 모른다.** */
  token_prefix: string
  last_seen_at: string | null
  created_at: string
  slots: AppSlot[]
  stream: AppStream | null
}

/** 등록 직후에만 나오는 모양. 토큰이 여기 한 번 실린다. */
export interface IssuedApp {
  app: App
  token: string
}

export interface NewApp {
  name: string
  slug: string
  description?: string | null
  homepage_url?: string | null
  events?: string[]
  event_url?: string | null
}

export interface AppPatch {
  name?: string
  description?: string | null
  events?: string[]
  event_url?: string | null
}

export interface NewSlot {
  slot: string
  kind: SlotKind
  label: string
  url_template?: string | null
  position?: number
}

/**
 * 화면 한 자리에 실릴 것 하나.
 *
 * 링크면 `url` 이 **채워진 주소**고(자리표는 서버가 채운다), 패널이면 `body`
 * 가 마크다운이다. 둘 중 하나만 찬다.
 */
export interface Contribution {
  app_slug: string
  app_name: string
  slot: string
  kind: SlotKind
  label: string
  url: string | null
  body: string | null
}

/** 다른 클라이언트와 같은 방식으로 접두사를 적는다 (`assets.ts` 참조). */
const BASE = '/api/v1/apps'

export function createPluginsApi(client: ApiClient) {
  return {
    /** 서버가 아는 자리 목록. **화면이 어휘를 자기가 들고 있지 않는다.** */
    slotCatalog: () => client.get<SlotCatalogEntry[]>(`${BASE}/slots`),

    /** 등록된 앱 전부. 앱은 사람이 하나씩 등록하는 것이라 커서를 두지 않았다. */
    list: () => client.get<App[]>(BASE),
    get: (id: string) => client.get<App>(`${BASE}/${id}`),
    /** 등록한다. **토큰은 이 응답에만 실린다.** */
    register: (body: NewApp) => client.post<IssuedApp>(BASE, body),
    update: (id: string, body: AppPatch) => client.patch<App>(`${BASE}/${id}`, body),
    setEnabled: (id: string, enabled: boolean) =>
      client.post<App>(`${BASE}/${id}/enabled`, { enabled }),
    /** 토큰을 새로 낸다. 옛 토큰은 즉시 죽는다. */
    rotateToken: (id: string) => client.post<{ token: string }>(`${BASE}/${id}/token`, {}),
    remove: (id: string) => client.delete<void>(`${BASE}/${id}`),

    place: (id: string, body: NewSlot) => client.post<AppSlot>(`${BASE}/${id}/slots`, body),
    unplace: (id: string, slotId: string) => client.delete<void>(`${BASE}/${id}/slots/${slotId}`),

    /** 이 이슈에서 앱들이 놓은 것. 이슈를 볼 수 있는 사람만 받는다. */
    forIssue: (issueId: string) =>
      client.get<Contribution[]>(`${BASE}/contributions/issue/${issueId}`),
    /** 설정 목록에 붙은 앱 링크. */
    forSettings: () => client.get<Contribution[]>(`${BASE}/contributions/settings`),
  }
}

export type PluginsApi = ReturnType<typeof createPluginsApi>
