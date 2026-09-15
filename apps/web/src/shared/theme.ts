/**
 * 밝게 / 어둡게.
 *
 * 어두운 팔레트는 `tokens.css` 에 처음부터 있었다. **켜는 길이 없었을**
 * 뿐이다 — 정의만 해 두고 손잡이를 안 만든 기능은 없는 기능과 같고, 그
 * 상태로 몇 달을 보냈다.
 *
 * ## `data-theme` 을 늘 찍는다
 *
 * CSS 는 `[data-theme='dark']` 한 갈래만 안다. "시스템을 따른다" 를
 * 미디어 질의로 또 쓰면 어두운 값 스무 개를 **두 벌** 적게 되고, 그 둘은
 * 반드시 어긋난다. 대신 여기서 시스템을 읽어 같은 속성으로 옮긴다.
 *
 * ## 첫 칠하기 전에 정해야 한다
 *
 * React 가 붙은 뒤에 속성을 찍으면 어두운 설정인 사람에게 흰 화면이 한 번
 * 번쩍인다. `index.html` 의 짧은 스크립트가 같은 규칙으로 먼저 찍고, 여기는
 * 그 뒤를 이어받는다 — 그래서 `STORAGE_KEY` 와 `resolve` 의 규칙이 그쪽과
 * 같아야 한다.
 */

export const THEMES = ['system', 'light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

export const STORAGE_KEY = 'ieum.theme'

function isTheme(value: unknown): value is Theme {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value)
}

/**
 * 저장된 선택. 프라이빗 모드에서는 `localStorage` 접근 자체가 던진다 —
 * 그때 화면이 죽는 것보다 기본값으로 서는 편이 낫다.
 */
export function readTheme(): Theme {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return isTheme(raw) ? raw : 'system'
  } catch {
    return 'system'
  }
}

export function writeTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* 프라이빗 모드 */
  }
}

/** 고른 것 + 시스템 설정 → 실제로 칠할 것. */
export function resolve(theme: Theme, systemPrefersDark: boolean): 'light' | 'dark' {
  if (theme === 'system') return systemPrefersDark ? 'dark' : 'light'
  return theme
}

export function prefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', resolve(theme, prefersDark()))
}
