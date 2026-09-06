import { RouterProvider } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { LoginScreen } from '@/features/auth/LoginScreen'
import { MfaEnrollScreen } from '@/features/auth/MfaEnrollScreen'
import { MfaScreen } from '@/features/auth/MfaScreen'
import { useAuthStore } from '@/features/auth/store'
import { authApi } from '@/shared/api'
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

  const me = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => authApi.me(),
    retry: false,
    enabled: stage === 'unknown' || stage === 'authenticated',
  })

  useEffect(() => {
    if (me.data) {
      setUser(me.data)
      return
    }
    if (!me.isError) return
    setStage(hasCode(me.error, 'auth.mfa_required') ? 'mfa-required' : 'anonymous')
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

function BootScreen() {
  const { t } = useTranslation(['common'])
  return (
    <div className="flex min-h-dvh items-center justify-center text-sm text-muted">
      {t('common:state.loading')}
    </div>
  )
}
