/**
 * 설정 화면들 사이의 탭.
 *
 * 상단 네비게이션에 설정 항목을 하나씩 늘리지 않는다 — 그러면 상단이 설정으로
 * 뒤덮인다.
 *
 * **두 묶음으로 나눈다.** 자기 계정 설정과 조직 관리는 다른 일이고, 열한 개를
 * 한 줄에 늘어놓으면 좁은 화면에서 넘치고 넓은 화면에서도 어디를 찾아야 할지
 * 모른다. 묶음에 이름을 붙여 소리로도 구분되게 한다.
 *
 * 권한이 없는 탭도 보여 준다. 화면 쪽에는 아직 사용자의 권한 목록이 없고,
 * 있는 척 숨기면 서버와 어긋난 순간 "메뉴가 사라졌다" 가 된다. 눌러서 들어간
 * 화면이 403 을 그대로 말해 주는 편이 정직하다.
 */

import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

const MINE = [
  { to: '/settings/tokens', labelKey: 'auth:tokens.title' },
  { to: '/settings/sessions', labelKey: 'auth:sessions.title' },
  { to: '/settings/passkeys', labelKey: 'auth:passkeys.title' },
] as const

const ORG = [
  { to: '/settings/people', labelKey: 'admin:users.title' },
  { to: '/settings/groups', labelKey: 'admin:groups.title' },
  { to: '/settings/roles', labelKey: 'admin:roles.title' },
  { to: '/settings/workflows', labelKey: 'admin:workflows.title' },
  { to: '/settings/fields', labelKey: 'admin:fields.title' },
  { to: '/settings/repositories', labelKey: 'vcs:title' },
  { to: '/settings/portals', labelKey: 'desk:portals.title' },
  { to: '/settings/customers', labelKey: 'desk:customers.title' },
  { to: '/settings/sla', labelKey: 'desk:sla.policies' },
  { to: '/settings/email-channels', labelKey: 'desk:email.title' },
  { to: '/settings/automation', labelKey: 'desk:automation.title' },
  { to: '/settings/audit', labelKey: 'admin:audit.title' },
  { to: '/settings/security', labelKey: 'admin:security.title' },
  { to: '/settings/sso', labelKey: 'admin:sso.title' },
] as const

export function SettingsNav() {
  const { t } = useTranslation(['auth', 'admin', 'desk', 'vcs'])

  return (
    <div className="flex flex-col gap-1">
      <Group label={t('admin:nav.mine')} tabs={MINE} />
      <Group label={t('admin:nav.organization')} tabs={ORG} />
    </div>
  )
}

function Group({
  label,
  tabs,
}: {
  label: string
  tabs: readonly { readonly to: string; readonly labelKey: string }[]
}) {
  const { t } = useTranslation(['auth', 'admin', 'desk', 'vcs'])

  return (
    <nav className="flex flex-wrap items-baseline gap-1 border-b border-border" aria-label={label}>
      <span className="pr-2 text-xs font-medium uppercase tracking-wide text-muted">{label}</span>
      {tabs.map((tab) => (
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
