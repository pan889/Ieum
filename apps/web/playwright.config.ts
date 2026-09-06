import { defineConfig, devices } from '@playwright/test'

/**
 * 브라우저 E2E.
 *
 * 여기서만 잡히는 게 있다: 클라이언트가 보내는 요청 본문이 서버 스키마와
 * 어긋나는데 서버가 200 을 주는 경우다. 유닛 테스트는 양쪽을 각각 통과시키고
 * 통합 테스트는 서버만 본다.
 *
 * API 와 웹 서버는 밖에서 띄운다 (Makefile 의 `make e2e`, CI 의 e2e 잡).
 * 여기서 띄우면 DB 준비·시드까지 설정이 들어와 로컬과 CI 가 갈라진다.
 */
/**
 * 브라우저 프로세스의 로케일을 못 박는다.
 *
 * POSIX 로케일에서는 크로미움이 내려받기 파일명의 비 ASCII 문자를 통째로
 * 버리고 `download` 로 바꾼다. 그러면 한글 파일명 검증이 조용히 무의미해진다
 * (실제로 그랬다 — 서버는 멀쩡한데 테스트만 못 보는 상태였다).
 */
const LAUNCH = { env: { ...process.env, LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' } }

export default defineConfig({
  testDir: './e2e',
  // 시드 DB 를 공유하므로 병렬로 돌리면 전역 목록 단언이 서로 밟는다.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 1 : 0,
  reporter: process.env['CI'] ? [['github'], ['list']] : [['list']],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env['E2E_BASE_URL'] ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          ...LAUNCH,
          // 브라우저가 이미 깔린 환경(개발 컨테이너 등)에서는 그걸 쓴다.
          // CI 는 `playwright install chromium` 으로 받으므로 비워 둔다.
          ...(process.env['E2E_CHROMIUM']
            ? { executablePath: process.env['E2E_CHROMIUM'] }
            : {}),
        },
      },
    },
  ],
})
