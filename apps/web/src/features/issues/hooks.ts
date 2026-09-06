import { useQuery } from '@tanstack/react-query'

import { issuesApi, projectsApi, usersApi } from '@/shared/api'

import { fetchAllProjects } from './allProjects'

/**
 * 프로젝트 목록. 필터·생성 폼이 공유한다.
 *
 * 한 페이지만 받지 않는다 — 잘린 목록은 "그 프로젝트가 없다" 와 구별되지
 * 않는다 (allProjects.ts 참고).
 */
export function useProjects() {
  return useQuery({
    queryKey: ['projects', 'all'],
    queryFn: () => fetchAllProjects(projectsApi),
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
