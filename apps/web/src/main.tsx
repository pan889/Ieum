import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'
import { initI18n } from '@/shared/i18n'
import '@/styles/tokens.css'

const queryClient = new QueryClient({
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

const container = document.getElementById('root')
if (!container) throw new Error('#root not found')

void initI18n().then(() => {
  createRoot(container).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  )
})
