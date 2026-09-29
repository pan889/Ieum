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
import { SOURCE_URL } from '@/shared/source'
import { Button } from '@/shared/ui/primitives'

const FOOT_SELECT =
  'h-8 w-full rounded-md border border-sidebar-border bg-sidebar px-2 text-xs text-sidebar-fg hover:bg-sidebar-hover'

import { BrandMark } from './BrandMark'
import { CommandPalette } from './CommandPalette'
import { NavIcon } from './NavIcon'
import { ShortcutHelp } from './ShortcutHelp'
import { readSidebarCollapsed, writeSidebarCollapsed } from './sidebar'

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
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)
  const drawer = useRef<HTMLElement>(null)
  const drawerClose = useRef<HTMLButtonElement>(null)
  const menuTrigger = useRef<HTMLButtonElement>(null)

  // 어느 것이 떠 있는지. 둘이 같이 뜨면 초점이 갈라지므로 하나만 둔다.
  const [overlay, setOverlay] = useState<'palette' | 'help' | null>(null)
  const searchBox = useRef<HTMLInputElement>(null)

  // 이미 뭔가 쳐 두었으면 골라 준다 — `/` 로 와서 바로 새 검색어를 친다.
  const focusSearch = useCallback(() => {
    if (window.matchMedia('(max-width: 639px)').matches) {
      setOverlay('palette')
      return
    }
    searchBox.current?.focus()
    searchBox.current?.select()
  }, [])

  const toggleSidebar = () => {
    const next = !collapsed
    writeSidebarCollapsed(next)
    setCollapsed(next)
  }

  useEffect(() => {
    if (!mobileOpen) return undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    drawerClose.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setMobileOpen(false)
        menuTrigger.current?.focus()
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(drawer.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), select:not([disabled])',
      ) ?? []).filter((element) => element.getClientRects().length > 0)
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [mobileOpen])

  /**
   * 목록의 처리 함수. **`Record<ShortcutId, …>` 라서 빠뜨리면 타입이 막는다** —
   * 도움말에만 있고 아무 일도 안 하는 키가 생길 수 없다.
   */
  const handlers: Record<GlobalShortcutId, () => void> = {
    // 토글이 아니라 열기다. 누르고 있어도 깜빡이지 않는다(`keys.ts` 참고).
    palette: () => { setMobileOpen(false); setOverlay('palette') },
    help: () => { setMobileOpen(false); setOverlay('help') },
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

  const currentLabel = pathname.startsWith('/settings/')
    ? t('common:nav.settings')
    : pathname.startsWith('/search')
      ? t('common:nav.search')
      : t(NAV.find((item) => item.to === '/'
        ? pathname === '/'
        : pathname.startsWith(item.to))?.labelKey ?? 'common:nav.home')

  return (
    <div className="flex h-dvh overflow-hidden">
      {mobileOpen ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-40 bg-fg/40 md:hidden"
          onClick={() => { setMobileOpen(false); menuTrigger.current?.focus() }}
        />
      ) : null}
      <nav
        ref={drawer}
        id="primary-navigation"
        aria-label={t('common:nav.navigation')}
        className={clsx(
          'fixed inset-y-0 left-0 z-50 w-[16.5rem] shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-fg shadow-overlay md:static md:z-auto md:shadow-none',
          mobileOpen ? 'flex' : 'hidden md:flex',
          collapsed ? 'md:w-[4.75rem]' : 'md:w-[15.5rem]',
        )}
      >
        <div className={clsx(
          'flex h-16 shrink-0 items-center gap-2.5 border-b border-sidebar-border px-4',
          collapsed && 'md:justify-center md:px-2',
        )}>
          <Link
            to="/"
            aria-label="Ieum"
            onClick={() => { setMobileOpen(false) }}
            className="flex min-w-0 items-center gap-2.5"
          >
            <BrandMark />
            <span className={clsx('min-w-0', collapsed && 'md:sr-only')}>
              <span className="block text-base font-semibold tracking-tight">Ieum</span>
            </span>
          </Link>
          <button
            ref={drawerClose}
            type="button"
            className="ml-auto grid size-9 place-items-center rounded-md text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-fg md:hidden"
            aria-label={t('common:nav.closeMenu')}
            onClick={() => { setMobileOpen(false); menuTrigger.current?.focus() }}
          >
            <NavIcon name="close" />
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-2.5 py-4">
          {([null, ...NAV_GROUPS] as const).map((group) => {
            const items = NAV.filter((item) => item.group === group)
            if (items.length === 0) return null
            return (
              <ul
                key={group ?? 'top'}
                className={clsx(
                  'flex flex-col gap-0.5',
                  group && collapsed && 'md:border-t md:border-sidebar-border md:pt-2',
                )}
              >
                {group ? (
                  <li
                    className={clsx(
                      'px-2.5 pb-1 pt-2 text-2xs font-semibold tracking-[0.04em] text-sidebar-muted',
                      collapsed && 'md:sr-only',
                    )}
                  >
                    {t(`common:nav.group.${group}`)}
                  </li>
                ) : null}
                {items.map((item) => {
                  // `/` 는 모든 경로의 접두사다. 홈만 정확히 맞춰야 한다 —
                  // 안 그러면 어느 화면에서든 홈이 같이 켜져 보인다.
                  const active = item.to === '/'
                    ? pathname === '/'
                    : item.to === '/settings/tokens'
                      ? pathname.startsWith('/settings/')
                      : pathname.startsWith(item.to)
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        aria-current={active ? 'page' : undefined}
                        title={collapsed ? t(item.labelKey) : undefined}
                        onClick={() => { setMobileOpen(false) }}
                        className={clsx(
                          'group relative flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors',
                          collapsed && 'md:justify-center md:px-0',
                          active
                            ? 'bg-sidebar-active font-semibold text-accent'
                            : 'text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-fg',
                        )}
                      >
                        <span className={active ? 'text-accent' : 'text-sidebar-muted'}>
                          <NavIcon name={item.icon} />
                        </span>
                        <span className={clsx('truncate', collapsed && 'md:sr-only')}>
                          {t(item.labelKey)}
                        </span>
                        {item.to === '/notifications' && (unread.data ?? 0) > 0 ? (
                          <span
                            className={clsx(
                              'ml-auto min-w-5 rounded-md bg-accent-soft px-1.5 text-center text-2xs font-semibold leading-5 text-accent',
                              collapsed && 'md:absolute md:right-0 md:top-0 md:size-2 md:min-w-0 md:overflow-hidden md:p-0 md:text-[0px]',
                            )}
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

        <div className="shrink-0 border-t border-sidebar-border p-3">
          {user && collapsed ? (
            <button
              type="button"
              aria-label={t('common:nav.openProfile')}
              onClick={toggleSidebar}
              className="mx-auto hidden size-9 place-items-center rounded-md border border-sidebar-border bg-sidebar-hover text-sm font-semibold text-sidebar-fg md:grid"
            >
              {user.display_name.trim().slice(0, 1).toUpperCase()}
            </button>
          ) : null}
          <div className={clsx(collapsed && 'md:hidden')}>
            {user ? (
              <div className="flex items-center gap-2.5 px-1 py-1.5">
                <span
                  aria-hidden="true"
                  className="grid size-8 shrink-0 place-items-center rounded-md bg-accent-soft text-xs font-semibold text-accent"
                >
                  {user.display_name.trim().slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-sidebar-fg">
                    {user.display_name}
                  </span>
                  <span className="block truncate text-2xs text-sidebar-muted">{user.email}</span>
                </span>
              </div>
            ) : null}

            <div className="mt-3 flex items-center gap-1.5">
              <label className="min-w-0 flex-1">
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

              <label className="min-w-0 flex-1">
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
                className="px-1.5 text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-fg"
                loading={signOut.isPending}
                onClick={() => { signOut.mutate(); }}
              >
                {t('common:nav.signOut')}
              </Button>
            </div>

            <a
              className="mt-2 block px-1 text-2xs text-sidebar-muted underline-offset-2 hover:text-sidebar-fg hover:underline"
              href={SOURCE_URL}
              target="_blank"
              rel="noreferrer"
            >
              {t('common:source.label')}
            </a>
          </div>
          {collapsed ? (
            <a
              href={SOURCE_URL}
              target="_blank"
              rel="noreferrer"
              title={t('common:source.label')}
              aria-label={t('common:source.label')}
              className="mx-auto mt-2 hidden size-9 items-center justify-center rounded-md text-2xs font-bold text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-fg md:flex"
            >
              &lt;/&gt;
            </a>
          ) : null}
        </div>
      </nav>

      <main className="min-w-0 flex-1 overflow-y-auto bg-bg">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface px-4 sm:px-6 lg:px-8">
          <button
            ref={menuTrigger}
            type="button"
            aria-label={t('common:nav.openMenu')}
            aria-controls="primary-navigation"
            aria-expanded={mobileOpen}
            onClick={() => { setMobileOpen(true) }}
            className="grid size-9 shrink-0 place-items-center rounded-md text-muted hover:bg-sunken hover:text-fg md:hidden"
          >
            <NavIcon name="menu" />
          </button>
          <button
            type="button"
            aria-label={collapsed ? t('common:nav.expandSidebar') : t('common:nav.collapseSidebar')}
            aria-controls="primary-navigation"
            aria-expanded={!collapsed}
            onClick={toggleSidebar}
            className="hidden size-9 shrink-0 place-items-center rounded-md text-muted hover:bg-sunken hover:text-fg md:grid"
          >
            <NavIcon name={collapsed ? 'expand' : 'collapse'} />
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
            <span className="hidden font-medium text-subtle sm:inline">{t('common:nav.workspace')}</span>
            <span aria-hidden="true" className="hidden text-border-strong sm:inline">/</span>
            <span className="truncate font-semibold text-fg">{currentLabel}</span>
          </div>

          <form
            className="hidden h-9 w-full max-w-[19rem] items-center gap-2 rounded-md border border-border-strong bg-surface px-3 transition-colors focus-within:border-accent sm:flex"
            role="search"
            onSubmit={(event) => {
              event.preventDefault()
              if (!query.trim()) return
              void navigate({ to: '/search', search: { q: query.trim(), offset: 0 } })
            }}
          >
            <span className="text-subtle"><NavIcon name="search" /></span>
            <input
              ref={searchBox}
              type="search"
              aria-label={t('common:nav.search')}
              placeholder={t('common:nav.searchPlaceholder')}
              className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-subtle focus:ring-0"
              value={query}
              onChange={(event) => { setQuery(event.target.value) }}
            />
            <kbd className="rounded border border-border-strong bg-surface px-1.5 text-2xs text-subtle">/</kbd>
          </form>
          <button
            type="button"
            aria-label={t('common:nav.search')}
            onClick={() => { setOverlay('palette') }}
            className="grid size-9 shrink-0 place-items-center rounded-md text-muted hover:bg-sunken hover:text-fg sm:hidden"
          >
            <NavIcon name="search" />
          </button>
          <Link
            to="/notifications"
            aria-label={(unread.data ?? 0) > 0
              ? t('notifications:list.unreadCount', { count: unread.data ?? 0, ns: 'notifications' })
              : t('common:nav.notifications')}
            className="relative grid size-9 shrink-0 place-items-center rounded-md text-muted hover:bg-sunken hover:text-fg"
          >
            <NavIcon name="notifications" />
            {(unread.data ?? 0) > 0 ? (
              <span className="absolute right-1 top-1 size-2 rounded-full bg-danger ring-2 ring-surface" />
            ) : null}
          </Link>
          {pathname === '/issues' || pathname === '/issues/new' ? null : (
            <Link
              to="/issues/new"
              aria-label={t('common:keys.createIssue')}
              className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-md bg-accent px-2.5 text-sm font-medium text-accent-fg transition-[filter] hover:brightness-110 sm:px-3.5"
            >
              <NavIcon name="plus" />
              <span className="hidden lg:inline">{t('common:keys.createIssue')}</span>
            </Link>
          )}
        </header>
        <div className="mx-auto max-w-[80rem] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
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
