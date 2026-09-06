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
import { setLocale, type Locale } from '@/shared/i18n'

import { router } from './router'

/**
 * 단계 게이트.
 *
 * 서버가 상태의 단일 출처다: /auth/me 가 200 이면 인증 완료,
 * auth.mfa_required 면 2FA 가 남았다는 뜻이다. 클라이언트가 자체 판단하지
 * 않으므로 새로고침·다중 탭에서도 어긋나지 않는다.
 */
export function App() {
  const { i18n } = useTranslation()
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
  const userLocale = me.data?.locale
  useEffect(() => {
    if (userLocale && userLocale !== i18n.language) {
      void setLocale(userLocale as Locale)
    }
  }, [userLocale, i18n.language])

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
