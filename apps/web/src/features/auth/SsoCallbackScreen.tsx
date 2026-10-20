/**
 * IdP 가 돌려보내는 자리 (auth.md 4절).
 *
 * 앱 셸 **밖**에 있다. 라우터는 로그인한 뒤에만 올라오므로, 거기 넣으면
 * 로그인하려는 사람이 도착할 곳이 없다 — 초대 수락 화면과 같은 이유다.
 *
 * 주소창의 `code` 는 1회용이고 그대로 두면 브라우저 기록·리퍼러로 샌다.
 * 서버에 넘기는 즉시 주소에서 지운다.
 *
 * **OIDC 와 SAML 이 같은 화면을 쓴다.** 오는 값이 다르지만(OIDC 는
 * `code`+`state`, SAML 은 서버가 만든 1회용 코드 하나) 도착한 뒤에 하는 일은
 * 하나다: 토큰으로 바꾸고, 단계를 정하고, 주소를 치운다. 두 화면으로 두면
 * 한쪽만 고치는 날이 오고, 그때 SAML 사용자가 2FA 등록을 건너뛴다.
 */

import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { stageFor } from './stage'
import { useAuthStore, type AuthStage } from './store'

/** SAML ACS 가 브라우저를 되돌리는 자리. 서버의 `SAML_CALLBACK_PATH` 와 짝이다. */
export const SAML_CALLBACK_PATH = '/auth/saml/callback'

/**
 * 이 자리를 떠나 그 단계의 화면으로 간다.
 *
 * **주소를 먼저 `/` 로 바꾼다.** `App` 은 단계보다 먼저 **주소만 보고** 이
 * 화면을 고른다(`returning`). 주소를 콜백에 둔 채 단계만 `anonymous` 로
 * 내리면 `App` 은 다시 이 화면을 그리고, 이 화면은 이미 한 번 돌았으므로
 * "로그인하는 중…" 에 영원히 멈춘다 — 로그인 단추를 눌러도 같은 자리다.
 * 그래서 두 일을 한 곳에서, 이 순서로만 한다.
 */
function leave(setStage: (stage: AuthStage) => void, stage: AuthStage): void {
  window.history.replaceState({}, '', '/')
  setStage(stage)
}

export function SsoCallbackScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const started = useRef(false)
  /**
   * IdP 가 코드 대신 `error` 를 돌려보냈다(사람이 동의를 거절했거나 IdP 쪽
   * 설정이 틀렸다). **처음 렌더에서 읽는다** — 아래 effect 가 곧 주소를 지운다.
   *
   * IdP 가 준 `error_description` 은 **보여 주지 않는다.** 주소창의 글은 누구나
   * 지어 붙일 수 있어서, 그대로 띄우면 "고객센터 000 으로 전화하세요" 같은
   * 남의 문장을 우리 로그인 화면이 대신 말해 주는 셈이 된다.
   */
  const [denied] = useState(() => new URLSearchParams(window.location.search).has('error'))

  const complete = useMutation({
    mutationFn: ({ code, state }: { code: string; state: string | null }) =>
      // `state` 가 없으면 SAML 이다 — 그쪽은 서버가 흐름을 DB 에 들고 있어
      // 브라우저가 나를 증명할 값을 따로 지니지 않는다.
      state === null ? authApi.completeSaml(code) : authApi.completeSso(code, state),
    onSuccess: (tokens) => {
      // 로컬 로그인과 같은 판단을 쓴다. 여기서만 다르게 굴러가면 SSO 사용자가
      // 2FA 등록을 건너뛴다.
      leave(
        setStage,
        stageFor({
          required: tokens.mfa_required,
          enrollmentRequired: tokens.mfa_enrollment_required,
        }),
      )
    },
  })

  useEffect(() => {
    // React 18 의 개발용 이중 실행에서 코드를 두 번 쓰면 두 번째가 실패한다 —
    // 인가 코드는 1회용이다.
    if (started.current) return
    started.current = true

    const saml = window.location.pathname.startsWith(SAML_CALLBACK_PATH)
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    // 코드를 주소창에서 즉시 지운다. 기록에도 리퍼러에도 남으면 안 된다.
    window.history.replaceState({}, '', window.location.pathname)

    // 거절당한 것은 화면에 말하고 단추로 내보낸다(아래). 말없이 로그인
    // 화면으로 돌려보내면 사람은 단추를 잘못 눌렀나 하고 같은 일을 되풀이한다.
    if (denied) return
    if (!code || (!saml && !state)) {
      leave(setStage, 'anonymous')
      return
    }
    complete.mutate({ code, state: saml ? null : state })
    // 한 번만 돈다. complete 를 의존성에 넣으면 매 렌더마다 다시 보낸다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <AuthLayout title={t('auth:login.title')}>
      {denied || complete.isError ? (
        <div className="flex flex-col gap-4">
          <Alert>{denied ? t('auth:sso.failed') : describeError(complete.error)}</Alert>
          <Button onClick={() => { leave(setStage, 'anonymous') }}>
            {t('auth:login.submit')}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted">{t('auth:sso.signingIn')}</p>
      )}
    </AuthLayout>
  )
}
