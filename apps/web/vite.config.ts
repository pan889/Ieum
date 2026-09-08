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
    /**
     * **시간대를 못 박는다.**
     *
     * 이 컨테이너와 CI 러너는 UTC 다. 그러면 "로컬 달력의 오늘" 과 "UTC 의
     * 오늘" 이 언제나 같아서, 둘을 혼동한 코드가 시험을 그냥 통과한다 —
     * 그리고 UTC 가 아닌 곳의 사람에게만 기한이 하루 밀려 보인다. 일광절약
     * 시간이 없는 것도 마찬가지다: 하루를 23시간으로 세는 계산이 안 걸린다.
     *
     * 실제로 `due.ts` 의 두 판단(로컬 달력으로 오늘을 만든다 / UTC 자정으로
     * 날 수를 센다)을 되돌려 봤을 때, UTC 에서는 시험이 붉어지지 않았다.
     * 서울로 두면 둘 다 붉어진다.
     *
     * 시간대를 **찾아내는 능력으로** 고른다 — 제품의 기본값(서울)이 아니다.
     * UTC 가 못 보여 주는 것이 둘인데, 서울은 그중 하나만 보여 준다:
     *
     * | | UTC 와 어긋나나 | 일광절약시간이 있나 |
     * |---|---|---|
     * | UTC | 아니오 | 아니오 |
     * | Asia/Seoul | 예 | **아니오** (한국은 폐지했다) |
     * | America/New_York | 예 | 예 |
     *
     * 서울로 뒀을 때 "UTC 자정으로 날 수를 센다" 를 되돌려도 시험이 안
     * 붉어졌다. 뉴욕에서는 둘 다 붉어진다. 그래서 뉴욕이다.
     *
     * 뉴욕은 UTC 뒤이므로 "늦은 밤" 계열만 드러내지만, 시험이 이른 아침과
     * 늦은 밤을 **둘 다** 보므로 어느 쪽이든 잡힌다.
     */
    env: { TZ: 'America/New_York' },
    setupFiles: ['./src/test-setup.ts'],
    // e2e/ 는 playwright 가 돈다. 여기서 같이 잡으면 두 러너가 충돌한다.
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
