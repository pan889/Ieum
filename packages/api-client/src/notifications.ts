import type { ApiClient } from './client'

/** 워치할 수 있는 것. 서버의 `WATCH_TARGETS` 와 같아야 한다. */
export type WatchTarget = 'issue' | 'project' | 'page' | 'space'

/** 물어본 것 중 **구독 중인 것만**. 없는 것은 안 적는다. */
export interface WatchStatuses {
  watching: string[]
}

export interface Notification {
  id: string
  /** `issue.created`·`wiki.page.updated`… 목록에서 아이콘을 고를 때 쓴다. */
  kind: string
  /** 이미 수신자 언어로 렌더돼 있다. 화면이 다시 번역하지 않는다. */
  title: string
  body: string | null
  /** 앱 내부 경로. 서버는 절대 URL 을 저장하지 않는다(호스트가 바뀐다). */
  link: string | null
  target_type: string | null
  target_id: string | null
  actor_id: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationPage {
  items: Notification[]
  next_cursor: string | null
  unread_count: number
}

/** 메일을 언제 받을지. 켬/끔 하나로 두면 사람들이 통째로 끈다. */
export type EmailMode = 'instant' | 'daily' | 'off'
export const EMAIL_MODES: EmailMode[] = ['instant', 'daily', 'off']

export interface NotificationPreferences {
  in_app: boolean
  email_mode: EmailMode
  notify_own_actions: boolean
  muted_events: string[]
}

export interface WatchStatus {
  watching: boolean
}

const BASE = '/api/v1/notifications'
const WATCHES = '/api/v1/watches'

export function createNotificationsApi(client: ApiClient) {
  return {
    list: (params: { limit?: number; cursor?: string; unreadOnly?: boolean } = {}) => {
      const query = new URLSearchParams()
      if (params.limit) query.set('limit', String(params.limit))
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.unreadOnly) query.set('unread_only', 'true')
      const suffix = query.size > 0 ? `?${query.toString()}` : ''
      return client.get<NotificationPage>(`${BASE}${suffix}`)
    },

    /** `ids` 를 비우면 전부 읽음으로. 서버가 남의 알림은 건드리지 않는다. */
    markRead: (ids?: string[]) => client.post<void>(`${BASE}/read`, { ids: ids ?? null }),

    preferences: () => client.get<NotificationPreferences>(`${BASE}/preferences`),
    updatePreferences: (body: Partial<NotificationPreferences>) =>
      client.patch<NotificationPreferences>(`${BASE}/preferences`, body),

    watches: {
      status: (target: WatchTarget, id: string) =>
        client.get<WatchStatus>(
          `${WATCHES}/status?target_type=${target}&target_id=${encodeURIComponent(id)}`,
        ),
      /**
       * 여러 개를 **한 번에** 묻는다. 구독 중인 것만 돌아온다.
       *
       * 목록 화면이 행마다 `status` 를 부르면 한 페이지에 스무 번이다.
       * 그 무름은 `ProjectPicker` 머리에 세 번 데고 적어 둔 것과 같은
       * 종류라, 목록에 토글을 달기 전에 이 문을 먼저 냈다.
       */
      statuses: (target: WatchTarget, ids: string[]) => {
        if (ids.length === 0) return Promise.resolve({ watching: [] } as WatchStatuses)
        const query = new URLSearchParams({ target_type: target })
        for (const id of ids) query.append('target_ids', id)
        return client.get<WatchStatuses>(`${WATCHES}/statuses?${query.toString()}`)
      },
      start: (target: WatchTarget, id: string) =>
        client.post<WatchStatus>(WATCHES, { target_type: target, target_id: id }),
      stop: (target: WatchTarget, id: string) =>
        client.delete<WatchStatus>(WATCHES, { target_type: target, target_id: id }),
    },
  }
}

export type NotificationsApi = ReturnType<typeof createNotificationsApi>
