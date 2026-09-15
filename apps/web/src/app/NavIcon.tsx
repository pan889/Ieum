/**
 * 사이드바 아이콘.
 *
 * 이름표만 열세 개를 세로로 늘어놓으면 눈이 **글자를 읽어야** 어디가 어딘지
 * 안다. 아이콘이 있으면 자리로 기억한다 — 매일 스무 번 오가는 길이라 그
 * 차이가 크다.
 *
 * 아이콘 묶음을 받아 오지 않는다. 열세 개 때문에 의존성을 하나 더 두면
 * 번들에 수백 개가 따라 들어오고, 획 굵기가 우리 글자와 안 맞는다.
 * `stroke-width: 1.5` 로 통일한 단순한 형태만 직접 그린다.
 */
const PATHS: Record<string, string> = {
  home: 'M3 9.5 12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1V9.5Z',
  projects: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  issues: 'M4 6h16M4 12h16M4 18h10',
  boards: 'M4 4h5v16H4V4Zm6.5 0h5v10h-5V4ZM17 4h3v13h-3V4Z',
  sprints: 'M12 3a9 9 0 1 0 9 9M12 3v9l6.5 3.5M12 3a9 9 0 0 1 9 9',
  recurrences: 'M4 9a8 8 0 0 1 13.6-5.6L21 7M20 15a8 8 0 0 1-13.6 5.6L3 17M21 3v4h-4M3 21v-4h4',
  calendar: 'M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Zm0 4h16M8 3v4m8-4v4',
  gantt: 'M5 7h8M5 12h12M5 17h6',
  reports: 'M5 20V10m5 10V4m5 16v-7m5 7V8',
  desk: 'M4 13a8 8 0 0 1 16 0v4a3 3 0 0 1-3 3h-2M4 13v3a2 2 0 0 0 2 2h1v-6H6a2 2 0 0 0-2 2Zm16 0a2 2 0 0 0-2-2h-1v6h1a2 2 0 0 0 2-2v-2Z',
  wiki: 'M5 4h9l5 5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm9 0v5h5M8 13h8M8 17h5',
  notifications: 'M18 10a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6M10.3 20a2 2 0 0 0 3.4 0',
  settings:
    'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm8-3.5a8 8 0 0 0-.13-1.4l2-1.6-2-3.4-2.4 1a8 8 0 0 0-2.4-1.4L14.7 2h-4l-.37 2.6a8 8 0 0 0-2.4 1.4l-2.4-1-2 3.4 2 1.6a8.2 8.2 0 0 0 0 2.8l-2 1.6 2 3.4 2.4-1a8 8 0 0 0 2.4 1.4l.37 2.6h4l.37-2.6a8 8 0 0 0 2.4-1.4l2.4 1 2-3.4-2-1.6c.09-.46.13-.93.13-1.4Z',
}

export function NavIcon({ name }: { name: string }) {
  return (
    <svg
      // 뜻은 옆의 이름표가 말한다. 낭독기에 같은 말을 두 번 들려주지 않는다.
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={PATHS[name] ?? PATHS['issues']} />
    </svg>
  )
}
