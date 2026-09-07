import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { useAuthStore } from './store'
import { getAssertion, isSupported } from './webauthn'

/**
 * 로그인 후 2FA 확인.
 *
 * 세 가지가 같은 자리에서 끝난다: 인증기(패스키·보안 키), TOTP 코드, 백업
 * 코드. **등록해 둔 것이 무엇인지 물어서** 인증기를 먼저 보여 준다 — 패스키만
 * 쓰는 사람에게 코드 입력칸을 먼저 내밀면 넣을 것이 없다.
 */
export function MfaScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const [code, setCode] = useState('')
  const [useBackup, setUseBackup] = useState(false)
  const [byCode, setByCode] = useState(false)

  // 미완료 세션도 볼 수 있는 목록이다(auth.md 3절). 실패해도 코드 입력으로
  // 서야 한다 — 이 요청 하나 때문에 아무도 못 들어오면 안 된다.
  const credentials = useQuery({
    queryKey: ['mfa', 'credentials'],
    queryFn: () => authApi.mfaCredentials(),
    retry: false,
  })

  const verify = useMutation({
    mutationFn: () => authApi.verifyMfa(code.trim()),
    onSuccess: () => { setStage('authenticated') },
  })

  const passkey = useMutation({
    mutationFn: async () => {
      const { options } = await authApi.startPasskeyVerification()
      const response = await getAssertion(options)
      // 취소는 오류가 아니다. 조용히 되돌아온다.
      if (response === null) return false
      await authApi.finishPasskeyVerification(response)
      return true
    },
    onSuccess: (done) => { if (done) setStage('authenticated') },
  })

  const hasPasskey = (credentials.data ?? []).some((c) => c.kind === 'webauthn')
  const hasCode = (credentials.data ?? []).some((c) => c.kind === 'totp')
  // 목록을 못 읽었으면 코드 입력을 보여 준다 — 그것이 모두가 가진 길이다.
  const showPasskey = hasPasskey && isSupported() && !byCode
  const showCode = !showPasskey

  return (
    <AuthLayout title={t('auth:mfa.title')} subtitle={t('auth:mfa.subtitle')}>
      <div className="flex flex-col gap-4">
        {verify.isError ? <Alert>{describeError(verify.error)}</Alert> : null}
        {passkey.isError ? <Alert>{describeError(passkey.error)}</Alert> : null}

        {showPasskey ? (
          <>
            <Button loading={passkey.isPending} onClick={() => { passkey.mutate() }}>
              {passkey.isPending
                ? t('auth:mfa.passkeyPrompt')
                : t('auth:mfa.usePasskey')}
            </Button>
            {/* 인증기를 못 쓰는 상황(집에 두고 옴)에도 길이 있어야 한다.
                코드가 없으면 백업 코드가 그 길이다. */}
            <Button variant="ghost" onClick={() => { setByCode(true); setUseBackup(!hasCode) }}>
              {t('auth:mfa.useCode')}
            </Button>
          </>
        ) : null}

        {showCode ? (
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => { event.preventDefault(); verify.mutate() }}
          >
            <Field
              label={useBackup ? t('auth:mfa.backupCode') : t('auth:mfa.code')}
              name="code"
              // 6자리 숫자든 백업 코드든 자동완성이 끼어들면 방해만 된다.
              autoComplete="one-time-code"
              inputMode={useBackup ? 'text' : 'numeric'}
              required
              autoFocus
              value={code}
              onChange={(e) => { setCode(e.target.value) }}
            />
            <Button type="submit" loading={verify.isPending}>
              {t('auth:mfa.submit')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setUseBackup((v) => !v)
                setCode('')
              }}
            >
              {useBackup ? t('auth:mfa.useAuthenticator') : t('auth:mfa.useBackupCode')}
            </Button>
            {hasPasskey && isSupported() ? (
              <Button type="button" variant="ghost" onClick={() => { setByCode(false) }}>
                {t('auth:mfa.usePasskey')}
              </Button>
            ) : null}
          </form>
        ) : null}
      </div>
    </AuthLayout>
  )
}
