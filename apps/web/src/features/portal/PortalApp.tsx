/**
 * 고객 포털. **내부 앱 셸을 쓰지 않는다.**
 *
 * 이건 다른 표면이다: 고객이 보는 화면이고, 로그인 없이도 열리고, 내부
 * 네비게이션(프로젝트·이슈·설정)이 있으면 안 된다. `AppShell` 을 재사용하면
 * 고객에게 내부 메뉴가 보이고, 눌러 보면 전부 403 이다.
 *
 * 그래서 `App.tsx` 가 인증 게이트 **앞에서** 경로로 가른다 — `/invite` 와
 * `/auth/callback` 이 이미 그렇게 되어 있다. 로그인 뒤로 넘기면 게스트
 * 요청이 성립하지 않는다.
 *
 * 라우팅은 이 안에서 직접 한다. 세 화면뿐이고, 내부 라우터에 끼워 넣으면
 * `AppShell` 이 부모가 되기 때문이다.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAuthStore } from '@/features/auth/store'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert } from '@/shared/ui/primitives'

import { PortalForm } from './PortalForm'
import { PortalHome } from './PortalHome'
import { PortalTicket } from './PortalTicket'
import { navigate, parsePortalPath, rememberPortal, type PortalRoute } from './routes'

export function PortalApp() {
  const { t } = useTranslation(['desk', 'common'])
  const user = useAuthStore((s) => s.user)
  const [route, setRoute] = useState<PortalRoute | null>(() => parsePortalPath(location.pathname))

  // 뒤로 가기를 살린다. 이 표면은 링크로 오가므로 히스토리가 진짜로 필요하다.
  useEffect(() => {
    const onPop = () => { setRoute(parsePortalPath(location.pathname)) }
    window.addEventListener('popstate', onPop)
    return () => { window.removeEventListener('popstate', onPop) }
  }, [])

  const slug = route?.slug ?? ''
  const portal = useQuery({
    queryKey: ['portal', slug],
    queryFn: () => deskApi.portalInfo(slug),
    enabled: slug.length > 0,
    retry: false,
  })

  // 이 고객이 마지막으로 본 창구를 기억한다. 뿌리(`/`)로 들어온 고객 계정을
  // 어디로 보낼지 알아야 하기 때문이다 — 서버는 고객과 포털을 묶지 않는다.
  useEffect(() => {
    if (portal.data) rememberPortal(portal.data.slug)
  }, [portal.data])

  const go = (next: PortalRoute) => {
    navigate(next)
    setRoute(next)
  }

  if (!route) return <Missing message={t('desk:portal.notFound')} />
  if (portal.isError) {
    return <Missing message={describeError(portal.error)} />
  }
  if (!portal.data) {
    return <Missing message={t('common:state.loading')} />
  }

  return (
    <div className="min-h-dvh bg-bg">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-3xl items-baseline gap-3 px-4 py-4">
          <button
            type="button"
            className="text-lg font-semibold tracking-tight"
            onClick={() => { go({ kind: 'home', slug: portal.data.slug }) }}
          >
            {portal.data.name}
          </button>
          <span className="text-xs text-muted">{t('desk:portal.title')}</span>
          {/* 로그인한 고객에게는 보여 주지 않는다. 이미 들어와 있다. */}
          {user ? (
            <span className="ml-auto text-xs text-muted">{user.display_name}</span>
          ) : (
            <a className="ml-auto text-xs underline" href="/">
              {t('desk:portal.signIn')}
            </a>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        {route.kind === 'home' ? (
          <PortalHome portal={portal.data} onNavigate={go} />
        ) : null}
        {route.kind === 'form' ? (
          <PortalForm
            portal={portal.data}
            requestTypeId={route.requestTypeId}
            onNavigate={go}
          />
        ) : null}
        {route.kind === 'ticket' ? (
          <PortalTicket portal={portal.data} issueId={route.issueId} onNavigate={go} />
        ) : null}
      </main>
    </div>
  )
}

function Missing({ message }: { message: string }) {
  return (
    <div className="mx-auto max-w-lg px-4 py-16">
      <Alert>{message}</Alert>
    </div>
  )
}
