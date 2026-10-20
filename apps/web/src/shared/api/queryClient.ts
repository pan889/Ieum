import { QueryClient } from '@tanstack/react-query'

/**
 * 앱이 쓰는 **하나뿐인** 쿼리 캐시.
 *
 * `main.tsx` 안에 두면 React 밖에서는 닿을 수 없다. 세션을 잃었다는 신호는
 * API 클라이언트(`./index.ts`)가 React 밖에서 받으므로, 그 자리에서 캐시를
 * 버리려면 같은 인스턴스를 여기서 꺼내 써야 한다. 이 파일은 react-query 말고
 * 아무것도 import 하지 않는다 — 클라이언트와 서로를 부르는 고리가 안 생긴다.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 목록은 자주 바뀌지 않는다. 창을 오갈 때마다 재요청하면 시끄럽다.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // 4xx 는 재시도해도 같은 답이 온다. 잘못된 IQL 을 세 번 보내면
        // 사용자는 오류 하나에 서버 왕복 세 번을 기다린다.
        const status = (error as { status?: number }).status
        if (status !== undefined && status >= 400 && status < 500) return false
        return failureCount < 2
      },
    },
  },
})
