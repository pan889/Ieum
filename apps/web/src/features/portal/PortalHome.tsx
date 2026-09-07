/**
 * 포털 첫 화면. 요청 유형을 고르고, 로그인했으면 지난 요청을 본다.
 *
 * 로그인하지 않은 사람에게 "내 요청" 을 보여 주지 않는다 — 보여 줄 것이 없고,
 * 빈 목록은 "내 요청이 사라졌다" 로 읽힌다.
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { PortalInfo } from '@ieum/api-client'

import { useAuthStore } from '@/features/auth/store'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Card } from '@/shared/ui/primitives'

import type { PortalRoute } from './routes'

export function PortalHome({
  portal,
  onNavigate,
}: {
  portal: PortalInfo
  onNavigate: (route: PortalRoute) => void
}) {
  const { t, i18n } = useTranslation(['desk', 'common'])
  const user = useAuthStore((s) => s.user)

  const forms = useQuery({
    queryKey: ['portal', portal.slug, 'request-types'],
    queryFn: () => deskApi.portalRequestTypes(portal.slug),
  })

  const mine = useQuery({
    queryKey: ['portal', portal.slug, 'requests'],
    queryFn: () => deskApi.myTickets(portal.slug, { limit: 20 }),
    // 로그인하지 않았으면 아예 부르지 않는다. 부르면 401 이 나고, 그 401 은
    // 리프레시를 깨워 "세션이 끊겼다" 처리로 흘러간다.
    enabled: Boolean(user),
  })

  const dates = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' })

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('desk:portal.pick')}</h1>
          {portal.description ? (
            <p className="mt-1 text-sm text-muted">{portal.description}</p>
          ) : null}
        </div>

        {forms.isError ? <Alert>{describeError(forms.error)}</Alert> : null}

        {/* 실패했을 때 "폼이 없다" 고 말하지 않는다. 고객이 보는 화면에서
            그 거짓말이 특히 나쁘다 — 요청할 길이 없다고 믿고 떠난다. */}
        {forms.isSuccess && forms.data.length === 0 ? (
          <p className="text-sm text-muted">{t('desk:portal.pickEmpty')}</p>
        ) : null}

        <ul className="flex flex-col gap-2">
          {(forms.data ?? []).map((form) => (
            <li key={form.id}>
              <button
                type="button"
                className="w-full rounded-md border border-border bg-surface px-4 py-3 text-left hover:border-accent"
                onClick={() => {
                  onNavigate({ kind: 'form', slug: portal.slug, requestTypeId: form.id })
                }}
              >
                <span className="block text-sm font-medium">{form.name}</span>
                {form.description ? (
                  <span className="mt-0.5 block text-xs text-muted">{form.description}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      </section>

      {user ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
            {t('desk:portal.myRequests')}
          </h2>
          {mine.isError ? <Alert>{describeError(mine.error)}</Alert> : null}
          {mine.isSuccess && mine.data.items.length === 0 ? (
            <p className="text-sm text-muted">{t('desk:portal.myRequestsEmpty')}</p>
          ) : null}
          <ul className="flex flex-col gap-2">
            {(mine.data?.items ?? []).map((ticket) => (
              <li key={ticket.id}>
                <button
                  type="button"
                  className="flex w-full items-baseline gap-3 rounded-md border border-border bg-surface px-4 py-3 text-left hover:border-accent"
                  onClick={() => {
                    onNavigate({ kind: 'ticket', slug: portal.slug, issueId: ticket.id })
                  }}
                >
                  <span className="text-sm font-medium">{ticket.summary}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted">
                    {t('desk:portal.requestedOn', {
                      date: dates.format(new Date(ticket.created_at)),
                    })}
                  </span>
                  {/* 상태 **이름**은 관리자가 지은 데이터라 번역하지 않는다.
                      색은 category(시스템 값)가 정한다. */}
                  <StateBadge name={ticket.state_name} category={ticket.state_category} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <Card className="text-sm text-muted">{t('desk:portal.signedOut')}</Card>
      )}
    </div>
  )
}

/**
 * 상태 칩. **이름은 번역하지 않는다** — 관리자가 지은 데이터다(프로젝트
 * 이름과 같다). 색은 `category`(시스템 값)가 정하므로 상태를 뭐라고 짓든
 * 진행 중은 진행 중처럼 보인다.
 */
export function StateBadge({ name, category }: { name: string; category: string }) {
  const known = category === 'todo' || category === 'in_progress' || category === 'done'
  return <Badge tone={known ? category : 'neutral'}>{name}</Badge>
}
