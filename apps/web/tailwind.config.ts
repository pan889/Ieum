import type { Config } from 'tailwindcss'

/**
 * 디자인 토큰. 색은 CSS 변수로 두고 라이트/다크를 한곳에서 바꾼다.
 * Atlassian 의 팔레트·아이콘·상표는 쓰지 않는다 (D-43).
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
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
