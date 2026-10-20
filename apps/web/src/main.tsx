import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'
import { queryClient } from '@/shared/api/queryClient'
import { initI18n } from '@/shared/i18n'
import '@/styles/tokens.css'

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
