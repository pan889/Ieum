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
        // 401/403 은 재시도해도 같다. 권한 문제를 세 번 물어볼 이유가 없다.
        const status = (error as { status?: number }).status
        if (status === 401 || status === 403 || status === 404) return false
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
