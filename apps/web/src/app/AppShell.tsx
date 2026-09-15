import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAuthStore } from '@/features/auth/store'
import { useUnreadCount } from '@/features/notifications/NotificationsScreen'
import { authApi, usersApi } from '@/shared/api'
import { SUPPORTED_LOCALES, setLocale, type Locale } from '@/shared/i18n'
import { GLOBAL_SHORTCUTS, type GlobalShortcutId } from '@/shared/keys/catalog'
import { applyTheme, readTheme, THEMES, writeTheme, type Theme } from '@/shared/theme'
import { useShortcuts } from '@/shared/keys/useHotkeys'
import { Button } from '@/shared/ui/primitives'

/** 사이드바 바닥의 작은 고르개들. 둘이 같은 모양이어야 한 줄로 읽힌다. */
const FOOT_SELECT =
  'h-7 w-full rounded-md border border-transparent bg-transparent px-1.5 text-xs text-muted hover:border-border hover:bg-surface'

import { CommandPalette } from './CommandPalette'
import { NavIcon } from './NavIcon'
import { ShortcutHelp } from './ShortcutHelp'

/**
 * 사이드바.
 *
 * 열세 개를 한 줄로 늘어놓았더니 **평평한 목록**이 됐다. 매일 오가는 길인데
 * 어디가 일이고 어디가 문서이고 어디가 설정인지 글자를 읽어야 알았다.
 * 하는 일로 묶고, 묶음마다 이름을 붙이고, 아이콘으로 자리를 기억하게 한다.
 *
 * `group` 은 묶음 머리글의 번역 키다. `null` 이면 머리글 없이 맨 위에 붙는다
 * — 홈은 어느 묶음에도 안 들어간다.
 */
const NAV = [
  { to: '/', labelKey: 'common:nav.home', icon: 'home', group: null },

  { to: '/issues', labelKey: 'common:nav.issues', icon: 'issues', group: 'work' },
  { to: '/boards', labelKey: 'common:nav.boards', icon: 'boards', group: 'work' },
  { to: '/sprints', labelKey: 'common:nav.sprints', icon: 'sprints', group: 'work' },
  { to: '/calendar', labelKey: 'common:nav.calendar', icon: 'calendar', group: 'work' },
  { to: '/gantt', labelKey: 'common:nav.gantt', icon: 'gantt', group: 'work' },
  { to: '/recurrences', labelKey: 'common:nav.recurrences', icon: 'recurrences', group: 'work' },

  { to: '/projects', labelKey: 'common:nav.projects', icon: 'projects', group: 'places' },
  { to: '/wiki', labelKey: 'common:nav.wiki', icon: 'wiki', group: 'places' },
  { to: '/desk', labelKey: 'common:nav.desk', icon: 'desk', group: 'places' },
  { to: '/reports', labelKey: 'common:nav.reports', icon: 'reports', group: 'places' },

  {
    to: '/notifications',
    labelKey: 'common:nav.notifications',
    icon: 'notifications',
    group: 'you',
  },
  { to: '/settings/tokens', labelKey: 'common:nav.settings', icon: 'settings', group: 'you' },
] as const

/** 묶음 차례. 머리글 글자는 `common:nav.group.*` 에서 온다. */
const NAV_GROUPS = ['work', 'places', 'you'] as const

export function AppShell() {
  const { t, i18n } = useTranslation(['common', 'notifications'])
  const user = useAuthStore((s) => s.user)
  const reset = useAuthStore((s) => s.reset)
  const setUser = useAuthStore((s) => s.setUser)
  const queryClient = useQueryClient()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const unread = useUnreadCount()

  // 어느 것이 떠 있는지. 둘이 같이 뜨면 초점이 갈라지므로 하나만 둔다.
  const [overlay, setOverlay] = useState<'palette' | 'help' | null>(null)
  const searchBox = useRef<HTMLInputElement>(null)

  // 이미 뭔가 쳐 두었으면 골라 준다 — `/` 로 와서 바로 새 검색어를 친다.
  const focusSearch = useCallback(() => {
    searchBox.current?.focus()
    searchBox.current?.select()
  }, [])

  /**
   * 목록의 처리 함수. **`Record<ShortcutId, …>` 라서 빠뜨리면 타입이 막는다** —
   * 도움말에만 있고 아무 일도 안 하는 키가 생길 수 없다.
   */
  const handlers: Record<GlobalShortcutId, () => void> = {
    // 토글이 아니라 열기다. 누르고 있어도 깜빡이지 않는다(`keys.ts` 참고).
    palette: () => { setOverlay('palette') },
    help: () => { setOverlay('help') },
    createIssue: () => { void navigate({ to: '/issues/new' }) },
    search: focusSearch,
  }
  useShortcuts(GLOBAL_SHORTCUTS, handlers, 'keys.groupGlobal')

  /**
   * 화면 색. `index.html` 의 짧은 스크립트가 첫 칠하기 전에 이미 같은 값을
   * 찍어 뒀으므로, 여기는 **고른 것을 기억하고 바꿀** 책임만 진다.
   *
   * "시스템 설정" 을 고른 사람은 OS 를 바꿨을 때 앱도 따라와야 한다 — 그래서
   * 듣고 있다가 다시 칠한다. 다른 값을 고른 사람에게는 아무 일도 없다.
   */
  const [theme, setTheme] = useState<Theme>(readTheme)
  useEffect(() => {
    applyTheme(theme)
    if (theme !== 'system') return undefined
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const follow = () => { applyTheme('system') }
    media.addEventListener('change', follow)
    return () => { media.removeEventListener('change', follow) }
  }, [theme])

  const signOut = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => {
      reset()
      // 캐시를 통째로 버린다. 남겨 두면 다음에 들어온 사람이 앞사람의 목록을
      // 잠깐 보게 되고, `me` 는 로그아웃 때 받은 401 이 그대로 남아 다시
      // 불려지지 않는다 — 옆 칸에 이름이 아예 안 뜬다.
      queryClient.clear()
    },
  })

  /**
   * 언어 선택은 서버에 저장한다.
   *
   * 브라우저에만 두면 다른 기기에서 다시 영어로 열리고, 알림 메일도 서버가
   * `user.locale` 로 렌더하므로 옛 언어로 나간다.
   *
   * 저장이 **먼저**다. 화면부터 바꾸면 곧이어 도착하는 `me` 응답이 옛 언어를
   * 들고 와 되돌려 놓는다. 실패하면 아무것도 안 바뀌고 선택 상자가 제자리로
   * 돌아온다 — 바뀐 척하다 다음 새로고침에 되돌아가는 것보다 낫다.
   */
  const changeLanguage = useMutation({
    mutationFn: async (locale: Locale) => {
      const updated = await usersApi.updateMe({ locale })
      await setLocale(locale)
      return updated
    },
    onSuccess: (updated) => {
      setUser(updated)
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
  })

  return (
    <div className="flex min-h-dvh">
      <nav
        aria-label={t('common:nav.projects')}
        // 사이드바는 **가라앉은 면**이다. 본문(흰 표면)보다 뒤로 물러나야
        // 눈이 본문으로 간다 — 둘 다 흰색이면 화면이 한 장으로 붙어 버린다.
        className="flex w-[15rem] shrink-0 flex-col border-r border-border bg-sunken"
      >
        {/* 상호는 첫 화면으로 가는 길이다 — 사람이 거기를 누른다. */}
        <Link
          to="/"
          className="flex items-center gap-2 px-3 pb-3 pt-4 text-base font-semibold tracking-tight text-fg hover:text-accent"
        >
          {/* 글자만 있던 자리. 작은 표식 하나로 제품처럼 보인다. */}
          <span
            aria-hidden="true"
            className="grid size-6 place-items-center rounded-md bg-accent text-[0.8125rem] font-bold text-accent-fg"
          >
            I
          </span>
          Ieum
        </Link>

        {/* 어디서든 한 상자로 찾는다. 검색어는 URL 이 소유하므로 결과를
            그대로 링크로 넘길 수 있다. */}
        <form
          className="px-2 pb-2"
          role="search"
          onSubmit={(event) => {
            event.preventDefault()
            if (!query.trim()) return
            void navigate({ to: '/search', search: { q: query.trim(), offset: 0 } })
          }}
        >
          <input
            ref={searchBox}
            type="search"
            aria-label={t('common:nav.search')}
            placeholder={t('common:nav.searchPlaceholder')}
            className="h-8 w-full rounded-md border border-border-strong bg-surface px-2.5 text-sm text-fg placeholder:text-subtle"
            value={query}
            onChange={(e) => { setQuery(e.target.value) }}
          />
        </form>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-2 pb-3">
          {([null, ...NAV_GROUPS] as const).map((group) => {
            const items = NAV.filter((item) => item.group === group)
            if (items.length === 0) return null
            return (
              <ul key={group ?? 'top'} className="flex flex-col gap-px">
                {group ? (
                  <li
                    // 머리글은 **읽으라고** 두는 것이 아니라 덩어리를 가르려고
                    // 둔다. 그래서 작고 흐리다.
                    className="px-3 pb-1 pt-1 text-2xs font-semibold uppercase tracking-wider text-subtle"
                  >
                    {t(`common:nav.group.${group}`)}
                  </li>
                ) : null}
                {items.map((item) => {
                  // `/` 는 모든 경로의 접두사다. 홈만 정확히 맞춰야 한다 —
                  // 안 그러면 어느 화면에서든 홈이 같이 켜져 보인다.
                  const active =
                    item.to === '/' ? pathname === '/' : pathname.startsWith(item.to)
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        aria-current={active ? 'page' : undefined}
                        className={clsx(
                          'group relative flex h-8 items-center gap-2.5 rounded-md px-3 text-sm transition-colors',
                          active
                            ? 'bg-surface font-medium text-fg shadow-raised'
                            : 'text-muted hover:bg-surface/70 hover:text-fg',
                        )}
                      >
                        {/* 켜진 줄의 왼쪽 띠. 배경만으로는 흐린 화면에서
                            어느 것이 켜졌는지 잘 안 보인다. */}
                        {active ? (
                          <span
                            aria-hidden="true"
                            className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent"
                          />
                        ) : null}
                        <span className={active ? 'text-accent' : 'text-subtle'}>
                          <NavIcon name={item.icon} />
                        </span>
                        <span className="truncate">{t(item.labelKey)}</span>
                        {/* 안 읽은 알림이 있다는 사실만 표시한다. 0 을 그리면
                            "아무것도 없음" 을 계속 알리는 셈이다. */}
                        {item.to === '/notifications' && (unread.data ?? 0) > 0 ? (
                          <span
                            className="ml-auto min-w-5 rounded-full bg-accent px-1.5 text-center text-2xs font-semibold leading-5 text-accent-fg"
                            aria-label={t('notifications:list.unreadCount', {
                              count: unread.data ?? 0,
                              ns: 'notifications',
                            })}
                          >
                            {unread.data}
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            )
          })}
        </div>

        <div className="border-t border-border p-2">
          {/* 누구로 들어와 있는지가 먼저다. 이름과 메일을 같이 보여 준다 —
              계정을 두 개 쓰는 사람이 실제로 있고, 이름만으로는 못 가른다. */}
          {user ? (
            <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
              <span
                aria-hidden="true"
                className="grid size-6 shrink-0 place-items-center rounded-full bg-accent-soft text-2xs font-semibold text-accent"
              >
                {user.display_name.trim().slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-fg">
                  {user.display_name}
                </span>
                <span className="block truncate text-2xs text-subtle">{user.email}</span>
              </span>
            </div>
          ) : null}

          <div className="mt-1 flex items-center gap-1">
            <label className="flex min-w-0 flex-1 items-center gap-1.5 text-2xs text-subtle">
              <span className="sr-only">{t('common:language.label')}</span>
              <select
                aria-label={t('common:language.label')}
                className={FOOT_SELECT}
                value={i18n.language}
                disabled={changeLanguage.isPending}
                onChange={(e) => { changeLanguage.mutate(e.target.value as Locale) }}
              >
                {SUPPORTED_LOCALES.map((locale) => (
                  <option key={locale} value={locale}>
                    {t(`common:language.${locale}`)}
                  </option>
                ))}
              </select>
            </label>

            {/* 어두운 팔레트는 처음부터 토큰에 있었는데 **켜는 길이 없었다**
                (`shared/theme.ts`). 언어 옆이 제자리다 — 둘 다 "내 화면을
                어떻게 볼까" 이고, 하루에 한 번 만질까 말까 한 것들이다. */}
            <label className="flex min-w-0 flex-1 items-center gap-1.5 text-2xs text-subtle">
              <span className="sr-only">{t('common:theme.label')}</span>
              <select
                aria-label={t('common:theme.label')}
                className={FOOT_SELECT}
                value={theme}
                onChange={(e) => {
                  const next = e.target.value as Theme
                  writeTheme(next)
                  setTheme(next)
                }}
              >
                {THEMES.map((name) => (
                  <option key={name} value={name}>
                    {t(`common:theme.${name}`)}
                  </option>
                ))}
              </select>
            </label>

            <Button
              variant="ghost"
              size="sm"
              loading={signOut.isPending}
              onClick={() => { signOut.mutate(); }}
            >
              {t('common:nav.signOut')}
            </Button>
          </div>
        </div>
      </nav>

      {/* 본문은 **너비를 제한한다.** 24인치 모니터에서 표가 가로로 끝까지
          늘어나면 한 줄을 눈으로 따라가다 놓친다. 넓은 화면(간트·보드)은
          자기 안에서 가로로 스크롤한다. */}
      <main className="min-w-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-[80rem] px-6 py-6">
          <Outlet />
        </div>
      </main>

      {overlay === 'palette' ? (
        <CommandPalette
          places={NAV.map((item) => ({ to: item.to, label: t(item.labelKey) }))}
          onClose={() => { setOverlay(null) }}
        />
      ) : null}
      {overlay === 'help' ? <ShortcutHelp onClose={() => { setOverlay(null) }} /> : null}
    </div>
  )
}
