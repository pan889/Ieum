import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { useAuthStore } from './store'

/** 로그인 후 2FA 확인. TOTP 와 백업 코드를 같은 엔드포인트가 받는다. */
export function MfaScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const [code, setCode] = useState('')
  const [useBackup, setUseBackup] = useState(false)

  const verify = useMutation({
    mutationFn: () => authApi.verifyMfa(code.trim()),
    onSuccess: () => { setStage('authenticated'); },
  })

  return (
    <AuthLayout title={t('auth:mfa.title')} subtitle={t('auth:mfa.subtitle')}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          verify.mutate()
        }}
      >
        {verify.isError ? <Alert>{describeError(verify.error)}</Alert> : null}

        <Field
          label={useBackup ? t('auth:mfa.backupCode') : t('auth:mfa.code')}
          name="code"
          // 6자리 숫자든 백업 코드든 자동완성이 끼어들면 방해만 된다.
          autoComplete="one-time-code"
          inputMode={useBackup ? 'text' : 'numeric'}
          required
          autoFocus
          value={code}
          onChange={(e) => { setCode(e.target.value); }}
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
      </form>
    </AuthLayout>
  )
}
