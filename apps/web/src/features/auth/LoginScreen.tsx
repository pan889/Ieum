import { useMutation, useQuery } from '@tanstack/react-query'
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

  // IdP 가 없는 설치가 대부분이다. 실패해도 화면은 로컬 로그인으로 서야 한다 —
  // SSO 목록 하나 때문에 아무도 못 들어오면 안 된다.
  const providers = useQuery({
    queryKey: ['auth', 'sso-providers'],
    queryFn: () => authApi.ssoProviders(),
    retry: false,
    staleTime: 300_000,
  })

  const startSso = useMutation({
    // 종류에 따라 시작 경로가 갈린다. 여기서 찍으면 다른 종류의 IdP 버튼이
    // 조용히 아무 일도 하지 않는다.
    mutationFn: (provider: { id: string; kind: string }) =>
      provider.kind === 'saml'
        ? authApi.startSaml(provider.id)
        : authApi.startSso(provider.id),
    onSuccess: ({ authorization_url }) => {
      // 브라우저를 IdP 로 보낸다. 돌아올 자리는 서버가 정해 뒀다.
      window.location.assign(authorization_url)
    },
  })

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
        <Button type="submit" size="lg" className="w-full" loading={login.isPending}>
          {login.isPending ? t('auth:login.submitting') : t('auth:login.submit')}
        </Button>
      </form>

      {(providers.data ?? []).length > 0 ? (
        <div className="mt-6 flex flex-col gap-2">
          {/* 가운데 "or" 글자 하나가 허공에 떠 있었다. 선을 그어야 그것이
              **갈림길**이라는 것이 보인다. */}
          <p className="flex items-center gap-3 text-xs text-subtle">
            <span aria-hidden="true" className="h-px flex-1 bg-border" />
            {t('auth:sso.divider')}
            <span aria-hidden="true" className="h-px flex-1 bg-border" />
          </p>
          {startSso.isError ? <Alert>{describeError(startSso.error)}</Alert> : null}
          {(providers.data ?? []).map((provider) => (
            <Button
              key={provider.id}
              variant="secondary"
              className="w-full"
              loading={startSso.isPending}
              onClick={() => { startSso.mutate({ id: provider.id, kind: provider.kind }) }}
            >
              {t('auth:sso.continueWith', { name: provider.name })}
            </Button>
          ))}
        </div>
      ) : null}
    </AuthLayout>
  )
}

/**
 * 로그인·MFA·초대 수락이 함께 쓰는 바깥틀.
 *
 * **여기가 제품의 첫인상이다.** 전에는 회색 벌판 한가운데에 흰 상자 하나가
 * 놓여 있었고, 그 상자에는 이름도 표식도 없었다 — 어느 회사의 무엇에
 * 로그인하는 중인지 화면만 보고는 알 수 없었다. 표식을 상자 위에 세우고,
 * 배경에 아주 옅은 강조색 물을 들여 상자가 **떠 있게** 한다.
 */
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
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden px-4 py-12">
      {/*
        배경 물. `aria-hidden` 인 순수 장식이고, 토큰 색에 투명도를 얹으므로
        다크 모드에서도 따로 정의할 것이 없다.
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[28rem] bg-gradient-to-b from-accent/[0.07] to-transparent"
      />

      <div className="relative flex w-full max-w-sm flex-col items-center gap-6">
        <div className="flex items-center gap-2 text-lg font-semibold tracking-tight text-fg">
          <span
            aria-hidden="true"
            className="grid size-7 place-items-center rounded-lg bg-accent text-sm font-bold text-accent-fg shadow-raised"
          >
            I
          </span>
          Ieum
        </div>

        <Card className="w-full p-6 shadow-overlay">
          <h1 className="text-lg font-semibold text-fg">{title}</h1>
          {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
          <div className="mt-5">{children}</div>
        </Card>
      </div>
    </main>
  )
}
