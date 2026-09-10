import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // any 금지는 절대규칙 5다.
      '@typescript-eslint/no-explicit-any': 'error',
      // 캐스팅은 사유 주석 필수 (conventions.md) — 자동 검출은 안 되므로 경고로 둔다.
      '@typescript-eslint/consistent-type-assertions': [
        'warn',
        { assertionStyle: 'as', objectLiteralTypeAssertions: 'never' },
      ],
      '@typescript-eslint/restrict-template-expressions': [
        'error',
        { allowNumber: true },
      ],
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test-setup.ts'],
    rules: { '@typescript-eslint/no-unsafe-assignment': 'off' },
  },
  {
    // Playwright 픽스처는 `use()` 로 값을 넘긴다. React 의 use 훅이 아니다.
    files: ['e2e/**/*.ts'],
    rules: { 'react-hooks/rules-of-hooks': 'off' },
  },
  {
    // **시드 자격을 스펙에 적지 않는다.** `fixtures.ts` 가 환경에서 읽어
    // 내보내고(`ADMIN_EMAIL`·`ADMIN_PASSWORD`·`MFA_ADMIN`), 스펙은 그것을
    // 쓴다. 여기에 적어 두면 시드 비밀번호를 정해 두는 곳(CI)에서 로그인이
    // 전부 401 이 된다 — 로컬에서는 기본값이 맞아서 초록이다.
    //
    // 이 저장소에서 **두 번** 일어났다. 한 번은 스위트 전체가 붉었고
    // (roadmap 의 "CI 가 계속 붉었다"), 한 번은 `approvals.spec.ts` 하나가
    // 40분짜리 브라우저 잡의 끝에서 붉었다. 그래서 규칙으로 만든다.
    files: ['e2e/**/*.spec.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "Literal[value=/^seed-.*-password/]",
          message:
            '시드 비밀번호를 스펙에 적지 않는다 — fixtures 의 ADMIN_PASSWORD / MFA_ADMIN 을 쓴다.',
        },
      ],
    },
  },
)
