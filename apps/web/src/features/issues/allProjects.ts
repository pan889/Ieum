/**
 * 프로젝트 **전부**. 고르는 목록은 잘려 있으면 안 된다.
 *
 * 한 페이지(100개)만 받아 `<select>` 를 채우면, 101번째 프로젝트를 가진
 * 사람은 그 프로젝트에 이슈를 만들 방법이 아예 없다 — 화면에는 목록이
 * 멀쩡히 보이므로 없는 줄도 모른다. 실제로 E2E 가 프로젝트를 100개 넘게
 * 만들자 바로 드러났다.
 *
 * 커서를 끝까지 따라가되 상한을 둔다. 상한이 없으면 데이터가 이상할 때
 * 화면이 요청을 끝없이 낸다.
 */

import type { Project, ProjectsApi } from '@ieum/api-client'

/** 한 번에 받는 개수. 서버 상한과 맞춘다. */
export const PAGE_SIZE = 100

/** 따라갈 페이지 수 상한. 이보다 많으면 고르는 UI 자체를 바꿔야 한다. */
export const MAX_PAGES = 20

export async function fetchAllProjects(api: ProjectsApi): Promise<Project[]> {
  const items: Project[] = []
  let cursor: string | undefined

  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result = await api.list({ limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) })
    items.push(...result.items)
    if (!result.next_cursor) return items
    cursor = result.next_cursor
  }
  return items
}
