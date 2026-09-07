/**
 * IdP 가 돌려보내는 자리 (auth.md 4절).
 *
 * 앱 셸 **밖**에 있다. 라우터는 로그인한 뒤에만 올라오므로, 거기 넣으면
 * 로그인하려는 사람이 도착할 곳이 없다 — 초대 수락 화면과 같은 이유다.
 *
 * 주소창의 `code` 는 1회용이고 그대로 두면 브라우저 기록·리퍼러로 샌다.
 * 서버에 넘기는 즉시 주소에서 지운다.
 */

import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { stageFor } from './stage'
import { useAuthStore } from './store'

export function SsoCallbackScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const started = useRef(false)

  const complete = useMutation({
    mutationFn: ({ code, state }: { code: string; state: string }) =>
      authApi.completeSso(code, state),
    onSuccess: (tokens) => {
      // 로컬 로그인과 같은 판단을 쓴다. 여기서만 다르게 굴러가면 SSO 사용자가
      // 2FA 등록을 건너뛴다.
      setStage(
        stageFor({
          required: tokens.mfa_required,
          enrollmentRequired: tokens.mfa_enrollment_required,
        }),
      )
      window.history.replaceState({}, '', '/')
    },
  })

  useEffect(() => {
    // React 18 의 개발용 이중 실행에서 코드를 두 번 쓰면 두 번째가 실패한다 —
    // 인가 코드는 1회용이다.
    if (started.current) return
    started.current = true

    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    // 코드를 주소창에서 즉시 지운다. 기록에도 리퍼러에도 남으면 안 된다.
    window.history.replaceState({}, '', window.location.pathname)

    if (!code || !state) {
      setStage('anonymous')
      return
    }
    complete.mutate({ code, state })
    // 한 번만 돈다. complete 를 의존성에 넣으면 매 렌더마다 다시 보낸다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <AuthLayout title={t('auth:login.title')}>
      {complete.isError ? (
        <div className="flex flex-col gap-4">
          <Alert>{describeError(complete.error)}</Alert>
          <Button onClick={() => { setStage('anonymous') }}>{t('auth:login.submit')}</Button>
        </div>
      ) : (
        <p className="text-sm text-muted">{t('auth:sso.signingIn')}</p>
      )}
    </AuthLayout>
  )
}
