/**
 * IdP 등록 (auth.md 4절).
 *
 * 이 설정을 쥐면 **누구로든 로그인할 수 있다** — 발급자와 JWKS 를 바꾸면
 * 자기 키로 서명한 토큰이 통과한다. 그래서 서버가 step-up 을 요구하고,
 * 2FA 를 등록하지 않은 관리자는 여기서 막힌다. 화면은 그 거절을 그대로
 * 보여 준다(숨기면 왜 안 되는지 알 수 없다).
 *
 * 시크릿은 한 번 보내면 끝이다. 목록에도 응답에도 다시 나오지 않는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { NewIdentityProvider } from '@ieum/api-client'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { idpApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

const EMPTY: NewIdentityProvider = {
  name: '',
  issuer: '',
  client_id: '',
  client_secret: '',
  authorization_endpoint: '',
  token_endpoint: '',
  jwks_uri: '',
}

export function SsoScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<NewIdentityProvider>(EMPTY)
  const [domains, setDomains] = useState('')

  const providers = useQuery({ queryKey: ['idp'], queryFn: () => idpApi.list() })

  const create = useMutation({
    mutationFn: () =>
      idpApi.create({
        ...draft,
        // 쉼표로 적게 한다. 도메인이 하나뿐인 조직이 대부분이다.
        email_domains: domains
          .split(',')
          .map((d) => d.trim().toLowerCase())
          .filter(Boolean),
      }),
    onSuccess: async () => {
      setAdding(false)
      setDraft(EMPTY)
      setDomains('')
      await queryClient.invalidateQueries({ queryKey: ['idp'] })
    },
  })

  // 끄고 켜는 것은 한 쌍이다. 끄기만 있으면 일방통행이 되고, 같은 발급자로
  // 새로 등록하는 길은 서버가 막는다 — 그 IdP 를 영구히 잃는다.
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? idpApi.enable(id) : idpApi.disable(id),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['idp'] }) },
  })

  const set = (key: keyof NewIdentityProvider) => (value: string) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const rows = providers.data ?? []

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('admin:sso.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('admin:sso.description')}</p>
        </div>
        <Button className="ml-auto" onClick={() => { setAdding((v) => !v) }}>
          {t('admin:sso.add')}
        </Button>
      </header>

      {providers.isError ? <Alert>{describeError(providers.error)}</Alert> : null}

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => { event.preventDefault(); create.mutate() }}
          >
            {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
            <Field
              label={t('admin:sso.name')}
              required
              autoFocus
              value={draft.name}
              onChange={(e) => { set('name')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.issuer')}
              required
              placeholder="https://login.microsoftonline.com/…/v2.0"
              value={draft.issuer}
              onChange={(e) => { set('issuer')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.clientId')}
              required
              value={draft.client_id}
              onChange={(e) => { set('client_id')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.clientSecret')}
              type="password"
              required
              autoComplete="off"
              value={draft.client_secret}
              onChange={(e) => { set('client_secret')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.authorizationEndpoint')}
              required
              value={draft.authorization_endpoint}
              onChange={(e) => { set('authorization_endpoint')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.tokenEndpoint')}
              required
              value={draft.token_endpoint}
              onChange={(e) => { set('token_endpoint')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.jwksUri')}
              required
              value={draft.jwks_uri}
              onChange={(e) => { set('jwks_uri')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.groupsClaim')}
              placeholder="groups"
              value={draft.groups_claim ?? ''}
              onChange={(e) => { set('groups_claim')(e.target.value) }}
            />
            <Field
              label={t('admin:sso.emailDomains')}
              placeholder="corp.example.com, corp.example.co.kr"
              value={domains}
              onChange={(e) => { setDomains(e.target.value) }}
            />
            {/* 콜백 주소는 서버가 정한다. IdP 에 등록할 값을 그대로 보여 준다 —
                이걸 못 찾으면 설정이 끝나지 않는다. */}
            <p className="text-xs text-muted">
              {t('admin:sso.redirectHint', {
                uri: `${window.location.origin}/auth/callback`,
              })}
            </p>
            <div className="flex gap-2">
              <Button type="submit" loading={create.isPending} disabled={!draft.name.trim()}>
                {t('admin:sso.save')}
              </Button>
              <Button variant="ghost" onClick={() => { setAdding(false) }}>
                {t('common:action.cancel')}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}

      {rows.length === 0 && !providers.isPending ? (
        <p className="text-sm text-muted">{t('admin:sso.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label={t('admin:sso.title')}>
          {rows.map((provider) => (
            <li key={provider.id}>
              <Card className="flex items-center gap-3">
                <div className="flex min-w-0 flex-col gap-0.5">
                  <p className="text-sm font-medium">
                    {provider.name}
                    {/* 꺼진 것도 목록에 남는다. 표시가 없으면 왜 로그인
                        화면에 안 뜨는지 알 수 없다. */}
                    {!provider.is_enabled ? (
                      <span className="ml-2 text-xs font-normal text-muted">
                        {t('admin:sso.disabled')}
                      </span>
                    ) : null}
                  </p>
                  <p className="truncate text-xs text-muted">{provider.issuer}</p>
                  {provider.email_domains.length > 0 ? (
                    <p className="text-xs text-muted">{provider.email_domains.join(', ')}</p>
                  ) : null}
                </div>
                <Button
                  variant="ghost"
                  className="ml-auto shrink-0 text-xs"
                  loading={toggle.isPending}
                  onClick={() => {
                    toggle.mutate({ id: provider.id, enabled: !provider.is_enabled })
                  }}
                >
                  {provider.is_enabled ? t('admin:sso.disable') : t('admin:sso.enable')}
                </Button>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
