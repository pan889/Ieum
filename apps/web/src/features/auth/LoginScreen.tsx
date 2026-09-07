import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

import { stageFor } from './stage'
import { useAuthStore } from './store'

export function LoginScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const setStage = useAuthStore((s) => s.setStage)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const login = useMutation({
    mutationFn: () => authApi.login(email, password),
    onSuccess: (tokens) => {
      // 등록과 확인을 갈라 보낸다. 뭉뚱그리면 아직 등록도 안 한 사람에게
      // 코드를 넣으라는 화면이 뜨고, 만들 수 없는 코드라 계정이 잠긴다.
      setStage(
        stageFor({
          required: tokens.mfa_required,
          enrollmentRequired: tokens.mfa_enrollment_required,
        }),
      )
    },
  })

  return (
    <AuthLayout title={t('auth:login.title')} subtitle={t('auth:login.subtitle')}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          login.mutate()
        }}
      >
        {login.isError ? <Alert>{describeError(login.error)}</Alert> : null}

        <Field
          label={t('auth:login.email')}
          type="email"
          name="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => { setEmail(e.target.value); }}
        />
        <Field
          label={t('auth:login.password')}
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => { setPassword(e.target.value); }}
        />
        <Button type="submit" loading={login.isPending}>
          {login.isPending ? t('auth:login.submitting') : t('auth:login.submit')}
        </Button>
      </form>
    </AuthLayout>
  )
}

export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <Card className="w-full max-w-sm">
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
        <div className="mt-6">{children}</div>
      </Card>
    </main>
  )
}
