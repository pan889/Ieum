import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { useAuthStore } from './store'

/** 2FA 강제 등록 화면. 확인 코드를 넣기 전까지 활성화되지 않는다. */
export function MfaEnrollScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const [code, setCode] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null)

  const enrollment = useQuery({
    queryKey: ['auth', 'totp-enrollment'],
    queryFn: () => authApi.enrollTotp(),
    // 화면에 들어올 때 한 번만. 재요청하면 QR 이 바뀌어 사용자가 혼란스럽다.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  const confirm = useMutation({
    mutationFn: async () => {
      if (!enrollment.data) throw new Error('enrollment not ready')
      await authApi.confirmTotp(enrollment.data.credential_id, code.trim())
      return authApi.issueBackupCodes()
    },
    onSuccess: (result) => { setBackupCodes(result.codes); },
  })

  if (backupCodes) {
    return (
      <AuthLayout
        title={t('auth:backupCodes.title')}
        subtitle={t('auth:backupCodes.description')}
      >
        <ul className="grid grid-cols-2 gap-2 font-mono text-sm">
          {backupCodes.map((backupCode) => (
            <li key={backupCode} className="rounded border border-border px-2 py-1.5">
              {backupCode}
            </li>
          ))}
        </ul>
        <Button className="mt-6 w-full" onClick={() => { setStage('authenticated'); }}>
          {t('common:action.confirm')}
        </Button>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title={t('auth:enroll.title')} subtitle={t('auth:enroll.subtitle')}>
      {enrollment.isPending ? <p className="text-sm text-muted">{t('common:state.loading')}</p> : null}
      {enrollment.isError ? <Alert>{describeError(enrollment.error)}</Alert> : null}

      {enrollment.data ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            confirm.mutate()
          }}
        >
          <div
            className="mx-auto w-44 rounded bg-white p-2 [&_svg]:size-full"
            // 서버가 만든 SVG QR 이다. otpauth URI 를 인코딩한 도형뿐이라
            // 사용자 입력이 섞이지 않는다.
            dangerouslySetInnerHTML={{ __html: enrollment.data.qr_svg }}
          />
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">{t('auth:enroll.manualKey')}</summary>
            <code className="mt-1 block break-all font-mono">{enrollment.data.secret}</code>
          </details>

          {confirm.isError ? <Alert>{describeError(confirm.error)}</Alert> : null}

          <Field
            label={t('auth:mfa.code')}
            name="code"
            autoComplete="one-time-code"
            inputMode="numeric"
            required
            value={code}
            onChange={(e) => { setCode(e.target.value); }}
          />
          <Button type="submit" loading={confirm.isPending}>
            {t('auth:enroll.confirm')}
          </Button>
        </form>
      ) : null}
    </AuthLayout>
  )
}
