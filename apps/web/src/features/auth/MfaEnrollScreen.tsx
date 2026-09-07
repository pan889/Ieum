import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'
import { useAuthStore } from './store'
import { createCredential, isSupported } from './webauthn'

/**
 * 2FA 강제 등록 화면.
 *
 * **무엇으로 등록할지 먼저 고르게 한다.** 인증기 앱 등록은 서버에 미확인
 * 자격증명을 만들면서 시작하므로, 화면에 들어오자마자 부르면 패스키를 고른
 * 사람의 계정에 쓰지 않을 TOTP 행이 남는다. 고른 뒤에 부른다.
 *
 * 어느 쪽이든 끝나면 백업 코드를 발급한다 — 인증기를 잃었을 때의 유일한
 * 복구 수단이다.
 */
export function MfaEnrollScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const [code, setCode] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null)
  const [byApp, setByApp] = useState(false)

  const enrollment = useQuery({
    queryKey: ['auth', 'totp-enrollment'],
    queryFn: () => authApi.enrollTotp(),
    // 고른 뒤에만 부른다.
    enabled: byApp,
    // 한 번만. 재요청하면 QR 이 바뀌어 사용자가 혼란스럽다.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  const passkey = useMutation({
    mutationFn: async () => {
      const { options } = await authApi.startPasskeyRegistration()
      const response = await createCredential(options)
      // 취소는 오류가 아니다. 고르는 화면으로 되돌아온다.
      if (response === null) return null
      await authApi.finishPasskeyRegistration(response)
      // 등록이 이 세션을 열어 준다(서버가 그렇게 한다). 그래서 곧바로
      // 백업 코드를 받을 수 있다 — 못 받으면 복구 수단이 없다.
      return authApi.issueBackupCodes()
    },
    onSuccess: (result) => { if (result) setBackupCodes(result.codes) },
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

  if (!byApp) {
    return (
      <AuthLayout title={t('auth:enroll.title')} subtitle={t('auth:enroll.subtitle')}>
        <div className="flex flex-col gap-3">
          {passkey.isError ? <Alert>{describeError(passkey.error)}</Alert> : null}
          <p className="text-sm text-muted">{t('auth:enroll.chooseHint')}</p>
          {isSupported() ? (
            <Button loading={passkey.isPending} onClick={() => { passkey.mutate() }}>
              {t('auth:enroll.choosePasskey')}
            </Button>
          ) : null}
          <Button
            variant={isSupported() ? 'secondary' : 'primary'}
            onClick={() => { setByApp(true) }}
          >
            {t('auth:enroll.chooseApp')}
          </Button>
        </div>
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
