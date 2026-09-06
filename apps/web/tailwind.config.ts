import type { Config } from 'tailwindcss'

/**
 * 디자인 토큰. 색은 CSS 변수로 두고 라이트/다크를 한곳에서 바꾼다.
 * Atlassian 의 팔레트·아이콘·상표는 쓰지 않는다 (D-43).
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  /**
   * 소스에 글자로 없는 클래스. Tailwind 는 `@layer components` 의 규칙도
   * 쓰이지 않으면 걷어내는데, 강조 상자의 톤은 **문서 본문**에서 온다
   * (`:::warning` 이라고 쓴 사람이 정한다) — 스캐너가 볼 수 있는 자리가
   * 아예 없다. 안 적어 두면 띠 색이 전부 회색으로 나온다(실제로 그랬다).
   */
  safelist: [
    'ieum-admonition-info',
    'ieum-admonition-note',
    'ieum-admonition-tip',
    'ieum-admonition-warning',
    'ieum-admonition-danger',
  ],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--ieum-bg) / <alpha-value>)',
        surface: 'rgb(var(--ieum-surface) / <alpha-value>)',
        'surface-raised': 'rgb(var(--ieum-surface-raised) / <alpha-value>)',
        border: 'rgb(var(--ieum-border) / <alpha-value>)',
        fg: 'rgb(var(--ieum-fg) / <alpha-value>)',
        muted: 'rgb(var(--ieum-muted) / <alpha-value>)',
        accent: 'rgb(var(--ieum-accent) / <alpha-value>)',
        'accent-fg': 'rgb(var(--ieum-accent-fg) / <alpha-value>)',
        danger: 'rgb(var(--ieum-danger) / <alpha-value>)',
        warning: 'rgb(var(--ieum-warning) / <alpha-value>)',
        success: 'rgb(var(--ieum-success) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Pretendard', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { card: '0.625rem' },
    },
  },
  plugins: [],
} satisfies Config
