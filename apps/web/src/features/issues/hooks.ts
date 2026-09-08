import { useQuery } from '@tanstack/react-query'

import { issuesApi, projectsApi, usersApi } from '@/shared/api'

/** 키로 프로젝트 하나 찾기에 받아 보는 개수. 정확히 맞는 하나만 쓴다. */
const BY_KEY_LIMIT = 20

/**
 * `useProjectByKey` 의 캐시 키. 피커가 방금 준 프로젝트를 미리 넣어 둘 때
 * 쓴다 — 안 넣으면 화면이 키만 보여 주다 한 박자 뒤에 이름으로 바뀐다.
 */
export function projectByKey(key: string) {
  return ['projects', 'byKey', key]
}

/**
 * 키 하나로 프로젝트를 찾는다. 목록 필터가 쓴다 — 필터는 URL 에서 **키**를
 * 받는데(`?project=ENG`), 화면에는 이름을 보여 줘야 한다.
 *
 * 전체 목록을 받아 훑지 않는다. 예전에는 커서를 끝까지 따라가는 함수로 그렇게
 * 했는데, 상한(2000개)에 닿는 순간 조용히 잘렸고 개발 DB 가 2334개가 되자
 * 필터에서 프로젝트가 사라졌다(ux-principles "잘린 선택 목록 — 세 번째").
 * 서버가 이미 키로 찾아 주므로 한 번만 물으면 된다.
 */
export function useProjectByKey(key: string | null) {
  return useQuery({
    // 키가 없으면 질의도 안 돈다. `projectByKey` 와 같은 모양을 유지해서
    // 미리 넣어 둔 값이 확실히 이 질의에 걸리게 한다.
    queryKey: key === null ? ['projects', 'byKey'] : projectByKey(key),
    queryFn: async () => {
      const page = await projectsApi.list({ q: key as string, limit: BY_KEY_LIMIT })
      // `q` 는 부분 일치다(`ENG` 가 `ENGX` 에도 맞는다). 정확히 그 키만 쓴다 —
      // 비슷한 이름을 대신 보여 주면 필터가 거짓말을 한다.
      return page.items.find((project) => project.key === key) ?? null
    },
    enabled: key !== null,
    staleTime: 60_000,
  })
}

export function useIssueTypes(projectId: string | null) {
  return useQuery({
    queryKey: ['issues', 'types', projectId],
    queryFn: () => issuesApi.types(projectId as string),
    enabled: projectId !== null,
    staleTime: 60_000,
  })
}

export function useWorkflowStates(projectId: string | null) {
  return useQuery({
    queryKey: ['issues', 'states', projectId],
    queryFn: () => issuesApi.states(projectId as string),
    enabled: projectId !== null,
    staleTime: 60_000,
  })
}

export function useFieldDefinitions(projectId: string | null, typeId: string | null) {
  return useQuery({
    queryKey: ['issues', 'fields', projectId, typeId],
    queryFn: () => issuesApi.fields(projectId as string, typeId as string),
    enabled: projectId !== null && typeId !== null,
    staleTime: 60_000,
  })
}

/**
 * id → 표시 이름. 목록이 이미 아는 담당자 id 만 조회한다.
 *
 * 전체 디렉터리를 받아 와서 훑으면 사용자가 늘어날수록 목록 화면이 느려지고,
 * 첫 페이지에 없는 담당자는 영원히 이름이 안 뜬다.
 */
export function useUserNames(ids: (string | null)[]) {
  const unique = [...new Set(ids.filter((id): id is string => id !== null))].sort()
  return useQuery({
    queryKey: ['users', 'names', unique],
    queryFn: async () => {
      const page = await usersApi.list({ ids: unique, limit: 100 })
      return new Map(page.items.map((u) => [u.id, u.display_name]))
    },
    enabled: unique.length > 0,
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  })
}

/** 담당자 선택기용 사용자 목록. 검색어가 있으면 서버에서 걸러 온다. */
export function useUserSearch(query: string) {
  return useQuery({
    queryKey: ['users', 'search', query],
    queryFn: () => usersApi.list(query ? { q: query, limit: 20 } : { limit: 20 }),
    staleTime: 30_000,
  })
}

/** version 종류 커스텀 필드의 선택지. 프로젝트별로 캐시한다. */
export function useVersions(projectId: string | null) {
  return useQuery({
    queryKey: ['issues', 'versions', projectId],
    queryFn: () => issuesApi.versions(projectId as string),
    enabled: projectId !== null,
    staleTime: 60_000,
  })
}
