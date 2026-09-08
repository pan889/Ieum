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
 *
 * **상한에 닿았다는 사실을 함께 돌려준다.** 안 그러면 막으려던 실패가 한 겹
 * 안쪽에서 그대로 되살아난다: 2000개에서 잘린 목록도 "그 프로젝트가 없다" 와
 * 구별되지 않고, 화면은 여전히 아무 말도 하지 않는다. 실제로 개발 DB 의
 * 프로젝트가 2334개가 되자 E2E 가 "방금 만든 프로젝트가 옵션에 없다" 로
 * 멈췄다 — 첫 페이지가 아니라 스무 번째 페이지 뒤로 밀렸을 뿐 증상은 같았다.
 *
 * 부르는 쪽은 `truncated` 를 **화면에 말해야** 한다. 말할 수 없는 자리라면
 * (2000개짜리 `<select>` 가 그렇다) 위젯이 틀린 것이다 — 검색으로 고르는
 * `ProjectPicker` 를 쓴다. 지금 고르는 화면은 모두 그쪽으로 옮겼다.
 */

import type { Project, ProjectsApi } from '@ieum/api-client'

/** 한 번에 받는 개수. 서버 상한과 맞춘다. */
export const PAGE_SIZE = 100

/** 따라갈 페이지 수 상한. 이보다 많으면 고르는 UI 자체를 바꿔야 한다. */
export const MAX_PAGES = 20

export interface AllProjects {
  items: Project[]
  /**
   * 상한에 닿아서 멈췄다 — 뒤에 더 있는데 안 받은 것이다.
   *
   * 참이면 `items` 는 "전부" 가 아니다. 이 값을 무시하고 목록을 그리면
   * 사용자는 없는 프로젝트와 안 받은 프로젝트를 구별할 수 없다.
   */
  truncated: boolean
}

export async function fetchAllProjects(api: ProjectsApi): Promise<AllProjects> {
  const items: Project[] = []
  let cursor: string | undefined

  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result = await api.list({ limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) })
    items.push(...result.items)
    if (!result.next_cursor) return { items, truncated: false }
    cursor = result.next_cursor
  }
  return { items, truncated: true }
}
