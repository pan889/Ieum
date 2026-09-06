import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // 개발 중에는 프록시로 붙어 same-origin 을 유지한다.
    // 세션 쿠키(SameSite=Lax)를 쓰려면 오리진이 같아야 편하다.
    proxy: {
      '/api': { target: process.env.VITE_API_BASE_URL ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    // e2e/ 는 playwright 가 돈다. 여기서 같이 잡으면 두 러너가 충돌한다.
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
