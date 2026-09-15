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
  { to: '/settings/assets', labelKey: 'desk:asset.title' },
  { to: '/settings/apps', labelKey: 'plugins:title' },
  { to: '/settings/imports', labelKey: 'imports:title' },
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
  const { t } = useTranslation(['auth', 'admin', 'desk', 'vcs', 'imports'])

  return (
    /*
      **한 상자에 담는다.** 전에는 밑줄 탭 스무 개가 맨 배경 위에서 네 줄로
      접혔고, 본문이 시작되기까지 190픽셀이 링크였다 — 화면마다 그 덩어리가
      제일 먼저 보이니 "설정" 이 아니라 "링크 목록" 이 주인공이 됐다.
      필터 막대와 같은 언어를 쓴다: 가라앉은 면, 도랑 라벨, 작은 알약.
    */
    <div className="flex flex-col divide-y divide-border rounded-card border border-border bg-sunken">
      <Group label={t('admin:nav.mine')} tabs={MINE} />
      <Group label={t('admin:nav.organization')} tabs={ORG} />
    </div>
  )
}

const TAB = 'rounded-md px-2 py-1 text-xs font-medium text-muted hover:bg-surface hover:text-fg'
const TAB_ON = 'rounded-md bg-surface px-2 py-1 text-xs font-semibold text-accent shadow-raised'

function Group({
  label,
  tabs,
}: {
  label: string
  tabs: readonly { readonly to: string; readonly labelKey: string }[]
}) {
  const { t } = useTranslation(['auth', 'admin', 'desk', 'vcs', 'imports'])

  return (
    <nav className="flex flex-wrap items-center gap-1 px-3 py-2" aria-label={label}>
      <span className="w-[6.5rem] shrink-0 text-2xs font-semibold uppercase tracking-wide text-subtle">
        {label}
      </span>
      {tabs.map((tab) => (
        <Link key={tab.to} to={tab.to} className={TAB} activeProps={{ className: TAB_ON }}>
          {t(tab.labelKey)}
        </Link>
      ))}
    </nav>
  )
}
