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
    // 스펙에서 **한 곳에서만 초록인 글자**를 막는다. 아래 둘은 둘 다 이
    // 저장소에서 실제로 일어났고, 둘 다 사람의 주의로는 두 번째를 못 막았다.
    files: ['e2e/**/*.spec.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          // **잘린 시각으로 이름을 짓지 않는다.** `String(Date.now()).slice(-6)`
          // 은 16.7분마다 같은 값으로 되돌아온다. 오래 쓴 개발 DB 에서 이름이
          // 겹치고, 그때 폼이 안 닫혀 "Save 단추가 둘" 로 엉뚱한 자리에서
          // 터진다. CI 는 매번 새 DB 라 **절대 안 보인다.**
          selector:
            "CallExpression[callee.property.name='slice'] CallExpression[callee.object.name='Date'][callee.property.name='now']",
          message:
            '시각을 잘라 이름을 만들지 않는다 — 잘린 시각은 주기적으로 되돌아오고(6자리는 16.7분), 오래 쓴 개발 DB 에서 이름이 겹친다. fixtures 의 uniqueKey() / uniqueSlug() 를 쓴다.',
        },
        {
          // **시드 자격을 스펙에 적지 않는다.** `fixtures.ts` 가 환경에서 읽어
          // 내보내고(`ADMIN_EMAIL`·`ADMIN_PASSWORD`·`MFA_ADMIN`), 스펙은 그것을
          // 쓴다. 여기에 적어 두면 시드 비밀번호를 정해 두는 곳(CI)에서 로그인이
          // 전부 401 이 된다 — 로컬에서는 기본값이 맞아서 초록이다. 이 저장소에서
          // **두 번** 일어났다(스위트 전체가 붉은 적 한 번, 40분짜리 잡의 끝에서
          // 하나가 붉은 적 한 번).
          selector: "Literal[value=/^seed-.*-password/]",
          message:
            '시드 비밀번호를 스펙에 적지 않는다 — fixtures 의 ADMIN_PASSWORD / MFA_ADMIN 을 쓴다.',
        },
      ],
    },
  },
)
