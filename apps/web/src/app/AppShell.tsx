import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAuthStore } from '@/features/auth/store'
import { useUnreadCount } from '@/features/notifications/NotificationsScreen'
import { authApi, usersApi } from '@/shared/api'
import { SUPPORTED_LOCALES, setLocale, type Locale } from '@/shared/i18n'
import { Button } from '@/shared/ui/primitives'

const NAV = [
  { to: '/', labelKey: 'common:nav.home' },
  { to: '/projects', labelKey: 'common:nav.projects' },
  { to: '/issues', labelKey: 'common:nav.issues' },
  { to: '/boards', labelKey: 'common:nav.boards' },
  { to: '/sprints', labelKey: 'common:nav.sprints' },
  { to: '/recurrences', labelKey: 'common:nav.recurrences' },
  { to: '/calendar', labelKey: 'common:nav.calendar' },
  { to: '/gantt', labelKey: 'common:nav.gantt' },
  { to: '/reports', labelKey: 'common:nav.reports' },
  { to: '/desk', labelKey: 'common:nav.desk' },
  { to: '/wiki', labelKey: 'common:nav.wiki' },
  { to: '/notifications', labelKey: 'common:nav.notifications' },
  { to: '/settings/tokens', labelKey: 'common:nav.settings' },
] as const

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
        className="flex w-56 shrink-0 flex-col border-r border-border bg-surface"
      >
        {/* 상호는 첫 화면으로 가는 길이다 — 사람이 거기를 누른다. */}
        <Link to="/" className="px-4 py-4 text-lg font-semibold tracking-tight hover:text-accent">
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
            type="search"
            aria-label={t('common:nav.search')}
            placeholder={t('common:nav.searchPlaceholder')}
            className="w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-fg placeholder:text-muted"
            value={query}
            onChange={(e) => { setQuery(e.target.value) }}
          />
        </form>

        <ul className="flex flex-1 flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <li key={item.to}>
              <Link
                to={item.to}
                className={clsx(
                  'block rounded-md px-3 py-2 text-sm',
                  pathname.startsWith(item.to)
                    ? 'bg-surface-raised font-medium text-fg'
                    : 'text-muted hover:bg-surface-raised hover:text-fg',
                )}
              >
                {t(item.labelKey)}
                {/* 안 읽은 알림이 있다는 사실만 표시한다. 0 을 그리면
                    "아무것도 없음" 을 계속 알리는 셈이다. */}
                {item.to === '/notifications' && (unread.data ?? 0) > 0 ? (
                  <span
                    className="ml-2 rounded-full bg-accent px-1.5 py-0.5 text-xs text-white"
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
          ))}
        </ul>

        <div className="border-t border-border p-3">
          {user ? (
            <p className="truncate px-1 pb-2 text-xs text-muted" title={user.email}>
              {user.display_name}
            </p>
          ) : null}

          <label className="flex items-center gap-2 px-1 pb-2 text-xs text-muted">
            <span>{t('common:language.label')}</span>
            <select
              className="rounded border border-border bg-surface px-1.5 py-1 text-xs text-fg"
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

          <Button
            variant="ghost"
            className="w-full justify-start"
            loading={signOut.isPending}
            onClick={() => { signOut.mutate(); }}
          >
            {t('common:nav.signOut')}
          </Button>
        </div>
      </nav>

      <main className="flex-1 overflow-auto px-8 py-6">
        <Outlet />
      </main>
    </div>
  )
}
