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
        sunken: 'rgb(var(--ieum-sunken) / <alpha-value>)',
        border: 'rgb(var(--ieum-border) / <alpha-value>)',
        'border-strong': 'rgb(var(--ieum-border-strong) / <alpha-value>)',
        fg: 'rgb(var(--ieum-fg) / <alpha-value>)',
        muted: 'rgb(var(--ieum-muted) / <alpha-value>)',
        subtle: 'rgb(var(--ieum-subtle) / <alpha-value>)',
        accent: 'rgb(var(--ieum-accent) / <alpha-value>)',
        'accent-fg': 'rgb(var(--ieum-accent-fg) / <alpha-value>)',
        'accent-soft': 'rgb(var(--ieum-accent-soft) / <alpha-value>)',
        danger: 'rgb(var(--ieum-danger) / <alpha-value>)',
        'danger-soft': 'rgb(var(--ieum-danger-soft) / <alpha-value>)',
        warning: 'rgb(var(--ieum-warning) / <alpha-value>)',
        'warning-soft': 'rgb(var(--ieum-warning-soft) / <alpha-value>)',
        success: 'rgb(var(--ieum-success) / <alpha-value>)',
        'success-soft': 'rgb(var(--ieum-success-soft) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Pretendard', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      /**
       * 글자 크기 단계. 전에는 사실상 14px 하나였고, 제목은 굵기만 달랐다 —
       * 그래서 화면에 들어왔을 때 **무엇부터 읽어야 하는지**가 없었다.
       * 큰 글자는 자간을 좁혀야 뭉치지 않는다.
       */
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
        xs: ['0.75rem', { lineHeight: '1.125rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.875rem', { lineHeight: '1.375rem' }],
        md: ['0.9375rem', { lineHeight: '1.5rem' }],
        lg: ['1.0625rem', { lineHeight: '1.625rem', letterSpacing: '-0.006em' }],
        xl: ['1.25rem', { lineHeight: '1.75rem', letterSpacing: '-0.012em' }],
        '2xl': ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.018em' }],
        '3xl': ['1.875rem', { lineHeight: '2.25rem', letterSpacing: '-0.022em' }],
      },
      borderRadius: { card: '0.625rem' },
      boxShadow: {
        raised: 'var(--ieum-shadow-raised)',
        overlay: 'var(--ieum-shadow-overlay)',
      },
    },
  },
  plugins: [],
} satisfies Config
