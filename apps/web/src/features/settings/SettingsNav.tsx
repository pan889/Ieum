/**
 * 설정 화면들 사이의 탭.
 *
 * 상단 네비게이션에 설정 항목을 하나씩 늘리지 않는다 — 곧 IdP·정책·사용자
 * 관리가 붙으면 상단이 설정으로 뒤덮인다.
 *
 * 권한이 없는 탭도 보여 준다. 화면 쪽에는 아직 사용자의 권한 목록이 없고,
 * 있는 척 숨기면 서버와 어긋난 순간 "메뉴가 사라졌다" 가 된다. 눌러서 들어간
 * 화면이 403 을 그대로 말해 주는 편이 정직하다.
 */

import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

const TABS = [
  { to: '/settings/tokens', labelKey: 'auth:tokens.title' },
  { to: '/settings/sessions', labelKey: 'auth:sessions.title' },
  { to: '/settings/passkeys', labelKey: 'auth:passkeys.title' },
  { to: '/settings/people', labelKey: 'admin:users.title' },
  { to: '/settings/groups', labelKey: 'admin:groups.title' },
  { to: '/settings/audit', labelKey: 'admin:audit.title' },
  { to: '/settings/security', labelKey: 'admin:security.title' },
  { to: '/settings/sso', labelKey: 'admin:sso.title' },
] as const

export function SettingsNav() {
  const { t } = useTranslation(['auth', 'admin'])

  return (
    <nav className="flex gap-1 border-b border-border" aria-label={t('auth:tokens.title')}>
      {TABS.map((tab) => (
        <Link
          key={tab.to}
          to={tab.to}
          className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted hover:text-fg"
          activeProps={{ className: '-mb-px border-b-2 border-accent px-3 py-2 text-sm text-fg' }}
        >
          {t(tab.labelKey)}
        </Link>
      ))}
    </nav>
  )
}
