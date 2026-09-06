import { useInfiniteQuery, useQuery } from '@tanstack/react-query'

import { wikiApi } from '@/shared/api'

/** 스페이스 목록. 커서로 이어 받는다 — 한 페이지만 받으면 나머지가 사라진다. */
export function useSpaces(search: string) {
  return useInfiniteQuery({
    queryKey: ['wiki', 'spaces', search],
    queryFn: ({ pageParam }) =>
      wikiApi.spaces.list({
        ...(pageParam ? { cursor: pageParam } : {}),
        ...(search.trim() ? { q: search.trim() } : {}),
      }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: (previous) => previous,
  })
}

export function useSpaceByKey(key: string | null) {
  return useQuery({
    queryKey: ['wiki', 'space', key],
    queryFn: () => wikiApi.spaces.getByKey(key as string),
    enabled: key !== null,
    staleTime: 60_000,
  })
}

/** 문서 트리. 볼 수 있는 것만 온다 — 걸러내기는 서버가 한다. */
export function useSpaceTree(spaceId: string | null) {
  return useQuery({
    queryKey: ['wiki', 'tree', spaceId],
    queryFn: () => wikiApi.spaces.tree(spaceId as string),
    enabled: spaceId !== null,
  })
}

export function usePageByPath(spaceKey: string | null, path: string | null) {
  return useQuery({
    queryKey: ['wiki', 'page', spaceKey, path],
    queryFn: () => wikiApi.pages.getByPath(spaceKey as string, path as string),
    enabled: spaceKey !== null && path !== null && path !== '',
  })
}

export function usePageHistory(pageId: string | null) {
  return useQuery({
    queryKey: ['wiki', 'history', pageId],
    queryFn: () => wikiApi.pages.versions(pageId as string),
    enabled: pageId !== null,
  })
}
