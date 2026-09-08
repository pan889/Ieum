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
      /**
       * `ws: true` 는 **같은 오리진으로 돌릴 때**를 위한 것이다.
       *
       * `VITE_API_BASE_URL` 을 비우면 REST 도 소켓도 이 프록시를 타는데,
       * 이 옵션이 없으면 업그레이드만 통과하지 못한다. 기본 개발 스택은 그
       * 값이 채워져 있어 브라우저가 API(`:8000`)로 직접 가므로 소켓도 여기를
       * 안 지난다 — 그래도 두 모드 중 하나만 되는 상태로 두지 않는다.
       *
       * 운영에서는 앞단(nginx/ALB)이 같은 일을 해야 한다: `Upgrade` 와
       * `Connection` 헤더를 그대로 넘기지 않으면 소켓만 안 붙는다.
       */
      '/api': {
        target: process.env.VITE_API_BASE_URL ?? 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
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
