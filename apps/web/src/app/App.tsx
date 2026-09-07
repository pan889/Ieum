import { RouterProvider } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { AcceptInviteScreen } from '@/features/auth/AcceptInviteScreen'
import { LoginScreen } from '@/features/auth/LoginScreen'
import { MfaEnrollScreen } from '@/features/auth/MfaEnrollScreen'
import { MfaScreen } from '@/features/auth/MfaScreen'
import { SAML_CALLBACK_PATH, SsoCallbackScreen } from '@/features/auth/SsoCallbackScreen'
import { stageFor } from '@/features/auth/stage'
import { useAuthStore } from '@/features/auth/store'
import { PortalApp } from '@/features/portal/PortalApp'
import { lastPortal } from '@/features/portal/routes'
import { authApi, deskApi } from '@/shared/api'
import { hasCode } from '@/shared/api/errors'
import { i18next, setLocale, type Locale } from '@/shared/i18n'

import { router } from './router'

/**
 * 단계 게이트.
 *
 * 서버가 상태의 단일 출처다: /auth/me 가 200 이면 인증 완료,
 * auth.mfa_required 면 2FA 가 남았다는 뜻이다. 클라이언트가 자체 판단하지
 * 않으므로 새로고침·다중 탭에서도 어긋나지 않는다.
 */
export function App() {
  const stage = useAuthStore((s) => s.stage)
  const setStage = useAuthStore((s) => s.setStage)
  const setUser = useAuthStore((s) => s.setUser)

  // 초대 수락은 로그인 **전에** 여는 화면이다. 인증 단계와 무관하게 먼저
  // 가른다 — 라우터에 넣으면 로그인한 사람만 초대를 받을 수 있게 된다.
  const invited = window.location.pathname === '/invite'
  // IdP 가 돌려보내는 자리도 로그인 **전에** 열려야 한다. 라우터에 넣으면
  // 로그인하려는 사람이 도착할 곳이 없다. OIDC 와 SAML 이 같은 화면을 쓴다.
  const returning =
    window.location.pathname === '/auth/callback' ||
    window.location.pathname.startsWith(SAML_CALLBACK_PATH)
  // 고객 포털도 로그인 **전에** 열려야 한다. 게스트 요청이 그 자리에서
  // 성립하지 않으면 "로그인 없이 요청" 이라는 기능 자체가 없다. 그리고
  // 내부 앱 셸을 쓰지 않는다 — 고객에게 내부 메뉴가 보이면 안 된다.
  const portal = window.location.pathname.startsWith('/portal/')

  const me = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => authApi.me(),
    retry: false,
    // 포털에서도 부른다. 로그인한 고객이면 "내 요청" 을 보여 줘야 하고,
    // 실패(401)는 게스트라는 뜻이지 오류가 아니다.
    enabled: !invited && !returning && (stage === 'unknown' || stage === 'authenticated'),
  })

  useEffect(() => {
    if (me.data) {
      setUser(me.data)
      return
    }
    if (!me.isError) return
    const required = hasCode(me.error, 'auth.mfa_required')
    const enrollmentRequired = hasCode(me.error, 'auth.mfa_enrollment_required')
    // 둘 다 아니면 그냥 로그인이 안 된 것이다.
    if (!required && !enrollmentRequired) {
      setStage('anonymous')
      return
    }
    setStage(stageFor({ required, enrollmentRequired }))
  }, [me.data, me.isError, me.error, setUser, setStage])

  // 사용자 설정 locale 이 브라우저 추정값을 이긴다 (i18n.md 3절).
  //
  // **서버 값이 바뀔 때만** 반응한다. 현재 언어를 의존성에 넣으면 언어를
  // 고르는 순간 이 효과가 다시 돌아 서버 값으로 되돌려 놓는다. 한동안 그
  // 상태였고, 그래서 ko 카탈로그가 다 채워져 있는데도 통째로 쓸 수 없었다.
  // 이제 선택은 서버에 저장되므로(AppShell) 다음 me 응답이 새 값을 들고
  // 오고, 여기서는 그때만 움직이면 된다.
  const userLocale = me.data?.locale
  useEffect(() => {
    if (userLocale && userLocale !== i18next.language) {
      void setLocale(userLocale as Locale)
    }
  }, [userLocale])

  if (invited) return <AcceptInviteScreen />
  // 세션이 열리면 stage 가 바뀌므로 이 화면은 스스로 물러난다.
  if (returning && stage !== 'authenticated') return <SsoCallbackScreen />
  // 단계와 무관하게 포털을 그린다. `unknown` 일 때 로딩 화면을 끼우면
  // 게스트는 매번 깜빡임을 보고, `anonymous` 면 로그인 화면이 포털을
  // 가로챈다 — 그게 게스트 요청을 막는 가장 쉬운 방법이다.
  if (portal) return <PortalApp />

  // **누구인지 알기 전에는 앱을 그리지 않는다.**
  //
  // 로그인은 `stage` 를 먼저 `authenticated` 로 올리고, `/auth/me` 는 그
  // 뒤에 도착한다. 그 사이에 라우터를 그리면 `/` 가 `/projects` 로 리다이렉트
  // 되고, 곧이어 고객임이 밝혀져 아래 화면으로 바뀐다 — 내용은 맞지만
  // **주소가 내부 화면에 남는다.** 고객이 새로고침하면 그 왕복을 다시 하고,
  // 주소창에는 자기와 무관한 `/projects` 가 적혀 있다. 실제로 그랬다.
  //
  // 그래서 잠깐 기다린다. 이 화면은 로그인 직후 한 번 스치는 것이고,
  // 새로고침 때는 `unknown` 단계가 이미 같은 화면을 보여 준다.
  if (stage === 'authenticated' && !me.data && !me.isError) {
    return <BootScreen />
  }

  // **고객 계정이 내부 앱에 들어왔다.** `/auth/me` 는 고객에게도 200 을
  // 주므로 여기까지 온다. 그대로 내부 셸을 그리면 메뉴는 다 보이는데 누르는
  // 곳마다 403 이다 — 망가진 앱을 보여 주는 것이 가장 나쁘다.
  if (stage === 'authenticated' && me.data?.is_customer === true) {
    return <CustomerElsewhereScreen />
  }

  switch (stage) {
    case 'unknown':
      return <BootScreen />
    case 'anonymous':
      return <LoginScreen />
    case 'mfa-required':
      return <MfaScreen />
    case 'mfa-enroll':
      return <MfaEnrollScreen />
    case 'authenticated':
      return <RouterProvider router={router} />
  }
}

/**
 * 고객 계정이 내부 앱 주소로 들어왔을 때.
 *
 * 서버는 고객과 포털을 묶지 않는다(한 고객이 여러 창구를 쓸 수 있다). 그래서
 * 어디로 보낼지는 **이 브라우저가 마지막으로 본 창구**로 정한다 — 포털을 한
 * 번이라도 열었으면 그 값이 있다. 없으면 추측하지 않고 로그아웃 길만 준다:
 * 모든 포털 목록을 고객에게 펼쳐 보이는 것은 이 화면이 할 일이 아니다.
 */
function CustomerElsewhereScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const reset = useAuthStore((s) => s.reset)
  const remembered = lastPortal()

  // 이 브라우저가 마지막으로 본 창구를 알면 **바로 보낸다.** 링크만 두면,
  // 포털에서 "로그인" 을 눌러 여기 도착한 고객이 한 번 더 눌러야 한다 —
  // 자기가 요청하지도 않은 화면에서. `replace` 다: 뒤로 가기가 이 화면으로
  // 되돌아오면 왕복이 된다.
  useEffect(() => {
    if (remembered) location.replace(`/portal/${remembered}`)
  }, [remembered])

  // 기억이 없으면 서버에 묻는다. 초대를 받아 비밀번호를 정한 고객이 이
  // 경우다 — 포털을 한 번도 열지 않은 브라우저에서 앱 뿌리로 들어온다.
  // 여기서 추측을 포기하면 "포털을 쓰세요" 라고만 적힌 막다른 화면이 된다.
  const portals = useQuery({
    queryKey: ['portal', 'mine'],
    queryFn: () => deskApi.myPortals(),
    enabled: !remembered,
    retry: false,
  })

  const only = portals.data?.length === 1 ? portals.data[0] : undefined
  useEffect(() => {
    if (only) location.replace(`/portal/${only.slug}`)
  }, [only])

  if (remembered || only || portals.isLoading) {
    // 이동하는 사이 잠깐 보이는 화면. 빈 화면보다 낫다.
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
        {t('common:state.loading')}
      </div>
    )
  }

  const many = portals.data ?? []

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col items-start justify-center gap-3 px-4">
      <p className="text-sm">{t('desk:portal.useThePortal')}</p>
      {/* 창구가 여럿이면 고르게 한다. 하나도 없으면 고를 것이 없으니 아무
          것도 그리지 않는다 — 빈 목록을 그리면 "여기서 골라야 하는데 비어
          있다" 로 읽힌다. */}
      {many.map((portal) => (
        <a
          key={portal.slug}
          className="text-sm font-medium text-accent underline"
          href={`/portal/${portal.slug}`}
        >
          {portal.name}
        </a>
      ))}
      <button
        type="button"
        className="text-xs text-muted underline"
        onClick={() => {
          void authApi.logout().finally(() => {
            reset()
          })
        }}
      >
        {t('common:nav.signOut')}
      </button>
    </div>
  )
}

function BootScreen() {
  const { t } = useTranslation(['common'])
  return (
    <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
      {t('common:state.loading')}
    </div>
  )
}
