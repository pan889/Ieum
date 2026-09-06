import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import { useAuthStore } from '@/features/auth/store'
import { authApi } from '@/shared/api'
import { SUPPORTED_LOCALES, setLocale, type Locale } from '@/shared/i18n'
import { Button } from '@/shared/ui/primitives'

const NAV = [
  { to: '/projects', labelKey: 'common:nav.projects' },
  { to: '/issues', labelKey: 'common:nav.issues' },
  { to: '/boards', labelKey: 'common:nav.boards' },
  { to: '/wiki', labelKey: 'common:nav.wiki' },
  { to: '/settings/tokens', labelKey: 'common:nav.settings' },
] as const

export function AppShell() {
  const { t, i18n } = useTranslation(['common'])
  const user = useAuthStore((s) => s.user)
  const reset = useAuthStore((s) => s.reset)
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  const signOut = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => { reset(); },
  })

  return (
    <div className="flex min-h-dvh">
      <nav
        aria-label={t('common:nav.projects')}
        className="flex w-56 shrink-0 flex-col border-r border-border bg-surface"
      >
        <div className="px-4 py-4 text-lg font-semibold tracking-tight">Ieum</div>

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
              onChange={(e) => void setLocale(e.target.value as Locale)}
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
