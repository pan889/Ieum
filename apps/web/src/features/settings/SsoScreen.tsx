/**
 * IdP 등록 (auth.md 4절).
 *
 * 이 설정을 쥐면 **누구로든 로그인할 수 있다** — 발급자와 검증 재료(OIDC 의
 * JWKS, SAML 의 인증서)를 바꾸면 자기 키로 서명한 토큰이 통과한다. 그래서
 * 서버가 step-up 을 요구하고, 2FA 를 등록하지 않은 관리자는 여기서 막힌다.
 * 화면은 그 거절을 그대로 보여 준다(숨기면 왜 안 되는지 알 수 없다).
 *
 * 시크릿과 비밀키는 한 번 보내면 끝이다. 목록에도 응답에도 다시 나오지 않는다.
 *
 * 꺼진 IdP 도 목록에 남는다. 사라지면 다시 켤 대상을 고를 수 없고, 같은
 * 발급자로 새로 등록하는 길은 서버가 막는다 — 그 IdP 를 영구히 잃는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { IdentityProvider, NewIdentityProvider, NewSamlProvider } from '@ieum/api-client'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { idpApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { formatDateTime } from '@/features/issues/format'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Field,
  Select,
  Textarea,
} from '@/shared/ui/primitives'

type Kind = 'oidc' | 'saml'

const EMPTY_OIDC: NewIdentityProvider = {
  name: '',
  issuer: '',
  client_id: '',
  client_secret: '',
  authorization_endpoint: '',
  token_endpoint: '',
  jwks_uri: '',
}

const EMPTY_SAML = {
  name: '',
  metadata_xml: '',
  entity_id: '',
  sso_url: '',
  certificates: '',
  sp_private_key: '',
  sp_certificate: '',
  want_encrypted: false,
  allow_idp_initiated: false,
  groups_attribute: '',
}

/** 여러 줄로 받은 인증서를 목록으로. 빈 줄은 버린다. */
function certificateLines(value: string): string[] {
  return value
    .split(/\n{2,}|\r?\n(?=-----BEGIN)/)
    .map((chunk) => chunk.trim())
    .filter(Boolean)
}

export function SsoScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [kind, setKind] = useState<Kind>('oidc')
  const [oidc, setOidc] = useState<NewIdentityProvider>(EMPTY_OIDC)
  const [saml, setSaml] = useState(EMPTY_SAML)
  const [domains, setDomains] = useState('')

  const providers = useQuery({ queryKey: ['idp'], queryFn: () => idpApi.list() })

  const emailDomains = () =>
    domains
      .split(',')
      .map((d) => d.trim().toLowerCase())
      .filter(Boolean)

  const done = async () => {
    setAdding(false)
    setOidc(EMPTY_OIDC)
    setSaml(EMPTY_SAML)
    setDomains('')
    await queryClient.invalidateQueries({ queryKey: ['idp'] })
  }

  const create = useMutation({
    mutationFn: () => {
      if (kind === 'oidc') {
        // 쉼표로 적게 한다. 도메인이 하나뿐인 조직이 대부분이다.
        return idpApi.create({ ...oidc, email_domains: emailDomains() })
      }
      const body: NewSamlProvider = {
        name: saml.name,
        want_encrypted: saml.want_encrypted,
        allow_idp_initiated: saml.allow_idp_initiated,
        email_domains: emailDomains(),
      }
      // 메타데이터를 붙였으면 나머지는 서버가 읽는다. 셋을 손으로 옮겨 적는
      // 동안 한 글자가 틀리면 "인증서가 틀렸다" 로만 보인다.
      if (saml.metadata_xml.trim()) body.metadata_xml = saml.metadata_xml.trim()
      else {
        body.entity_id = saml.entity_id.trim()
        body.sso_url = saml.sso_url.trim()
        body.certificates = certificateLines(saml.certificates)
      }
      if (saml.sp_private_key.trim()) body.sp_private_key = saml.sp_private_key.trim()
      if (saml.sp_certificate.trim()) body.sp_certificate = saml.sp_certificate.trim()
      if (saml.groups_attribute.trim()) body.groups_attribute = saml.groups_attribute.trim()
      return idpApi.createSaml(body)
    },
    onSuccess: done,
  })

  // 끄고 켜는 것은 한 쌍이다. 끄기만 있으면 일방통행이 되고, 같은 발급자로
  // 새로 등록하는 길은 서버가 막는다 — 그 IdP 를 영구히 잃는다.
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? idpApi.enable(id) : idpApi.disable(id),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['idp'] }) },
  })

  const setOidcField = (key: keyof NewIdentityProvider) => (value: string) => {
    setOidc((current) => ({ ...current, [key]: value }))
  }
  const setSamlField = (key: keyof typeof EMPTY_SAML) => (value: string | boolean) => {
    setSaml((current) => ({ ...current, [key]: value }))
  }

  const rows = providers.data ?? []
  const name = kind === 'oidc' ? oidc.name : saml.name

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
            <Select
              label={t('admin:sso.kind')}
              value={kind}
              onChange={(e) => { setKind(e.target.value as Kind) }}
            >
              <option value="oidc">{t('admin:sso.kind.oidc')}</option>
              <option value="saml">{t('admin:sso.kind.saml')}</option>
            </Select>

            {kind === 'oidc' ? (
              <>
                <Field
                  label={t('admin:sso.name')}
                  required
                  autoFocus
                  value={oidc.name}
                  onChange={(e) => { setOidcField('name')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.issuer')}
                  required
                  placeholder="https://login.microsoftonline.com/…/v2.0"
                  value={oidc.issuer}
                  onChange={(e) => { setOidcField('issuer')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.clientId')}
                  required
                  value={oidc.client_id}
                  onChange={(e) => { setOidcField('client_id')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.clientSecret')}
                  type="password"
                  required
                  autoComplete="off"
                  value={oidc.client_secret}
                  onChange={(e) => { setOidcField('client_secret')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.authorizationEndpoint')}
                  required
                  value={oidc.authorization_endpoint}
                  onChange={(e) => { setOidcField('authorization_endpoint')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.tokenEndpoint')}
                  required
                  value={oidc.token_endpoint}
                  onChange={(e) => { setOidcField('token_endpoint')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.jwksUri')}
                  required
                  value={oidc.jwks_uri}
                  onChange={(e) => { setOidcField('jwks_uri')(e.target.value) }}
                />
                <Field
                  label={t('admin:sso.groupsClaim')}
                  placeholder="groups"
                  value={oidc.groups_claim ?? ''}
                  onChange={(e) => { setOidcField('groups_claim')(e.target.value) }}
                />
              </>
            ) : (
              <>
                <Field
                  label={t('admin:sso.name')}
                  required
                  autoFocus
                  value={saml.name}
                  onChange={(e) => { setSamlField('name')(e.target.value) }}
                />
                <Textarea
                  label={t('admin:sso.metadataXml')}
                  hint={t('admin:sso.metadataHint')}
                  rows={5}
                  value={saml.metadata_xml}
                  onChange={(e) => { setSamlField('metadata_xml')(e.target.value) }}
                />
                {/* 메타데이터가 없을 때만 직접 받는다. 둘 다 채우게 하면
                    어느 쪽이 이기는지 화면에서 알 수 없다. */}
                {saml.metadata_xml.trim() ? null : (
                  <>
                    <Field
                      label={t('admin:sso.entityId')}
                      required
                      value={saml.entity_id}
                      onChange={(e) => { setSamlField('entity_id')(e.target.value) }}
                    />
                    <Field
                      label={t('admin:sso.ssoUrl')}
                      required
                      value={saml.sso_url}
                      onChange={(e) => { setSamlField('sso_url')(e.target.value) }}
                    />
                    <Textarea
                      label={t('admin:sso.certificates')}
                      hint={t('admin:sso.certificatesHint')}
                      rows={4}
                      value={saml.certificates}
                      onChange={(e) => { setSamlField('certificates')(e.target.value) }}
                    />
                  </>
                )}
                <Field
                  label={t('admin:sso.groupsClaim')}
                  placeholder="groups"
                  value={saml.groups_attribute}
                  onChange={(e) => { setSamlField('groups_attribute')(e.target.value) }}
                />
                <Textarea
                  label={t('admin:sso.spPrivateKey')}
                  rows={3}
                  autoComplete="off"
                  value={saml.sp_private_key}
                  onChange={(e) => { setSamlField('sp_private_key')(e.target.value) }}
                />
                <Textarea
                  label={t('admin:sso.spCertificate')}
                  rows={3}
                  value={saml.sp_certificate}
                  onChange={(e) => { setSamlField('sp_certificate')(e.target.value) }}
                />
                <Checkbox
                  label={t('admin:sso.wantEncrypted')}
                  checked={saml.want_encrypted}
                  onChange={(e) => { setSamlField('want_encrypted')(e.target.checked) }}
                />
                <Checkbox
                  label={t('admin:sso.allowIdpInitiated')}
                  hint={t('admin:sso.allowIdpInitiatedHint')}
                  checked={saml.allow_idp_initiated}
                  onChange={(e) => {
                    setSamlField('allow_idp_initiated')(e.target.checked)
                  }}
                />
              </>
            )}

            <Field
              label={t('admin:sso.emailDomains')}
              placeholder="corp.example.com, corp.example.co.kr"
              value={domains}
              onChange={(e) => { setDomains(e.target.value) }}
            />
            {/* 돌아올 주소는 서버가 정한다. IdP 에 등록할 값을 그대로 보여
                준다 — 이걸 못 찾으면 설정이 끝나지 않는다. */}
            <p className="text-xs text-muted">
              {kind === 'oidc'
                ? t('admin:sso.redirectHint', { uri: `${window.location.origin}/auth/callback` })
                : t('admin:sso.samlRedirectHint', { uri: '/api/v1/auth/saml/acs' })}
            </p>
            <div className="flex gap-2">
              <Button type="submit" loading={create.isPending} disabled={!name.trim()}>
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
                    <span className="ml-2 text-xs font-normal text-muted">
                      {t(`admin:sso.kind.${provider.kind}`)}
                    </span>
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
                  {provider.kind === 'saml' ? (
                    <p className="text-xs text-muted">
                      <a
                        className="underline"
                        href={`/api/v1/auth/saml/${provider.id}/metadata`}
                      >
                        {t('admin:sso.metadataLink')}
                      </a>
                      {' · '}
                      {t('admin:sso.certificateCount', {
                        count: provider.saml_certificate_count,
                      })}
                    </p>
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
              <Provisioning provider={provider} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * SCIM 프로비저닝 (M4).
 *
 * SSO 와 **같은 줄에** 둔다. IdP 한 곳이 로그인과 계정 밀어넣기를 함께
 * 담당하고, 관리자는 그 둘을 같은 화면에서 설정한다(Okta·Entra 도 앱 하나
 * 안에서 그렇게 한다).
 *
 * **토큰은 발급 직후 한 번만 보여 준다.** 다시 볼 방법이 없어야 재발급이
 * 유일한 복구 방법이 되고, 그러면 유출된 토큰은 반드시 죽는다 — PAT 과 같은
 * 규약이다. 화면도 그 사실을 말해 준다.
 */
function Provisioning({ provider }: { provider: IdentityProvider }) {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [issued, setIssued] = useState<string | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['idp'] })
  const issue = useMutation({
    mutationFn: () => idpApi.issueScimToken(provider.id),
    onSuccess: async (found) => {
      setIssued(found.token)
      await refresh()
    },
  })
  const stop = useMutation({
    mutationFn: () => idpApi.stopScim(provider.id),
    onSuccess: async () => {
      setIssued(null)
      await refresh()
    },
  })

  return (
    <div className="mt-1 flex flex-col gap-1 border-l-2 border-border pl-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-fg">{t('admin:scim.title')}</span>
        <span className="text-muted">
          {provider.scim_enabled
            ? t('admin:scim.on')
            : provider.scim_has_token
              ? t('admin:scim.paused')
              : t('admin:scim.off')}
        </span>
        {/* **마지막으로 온 시각을 말한다.** 안 보이면 프로비저닝이 도는지
            확인할 방법이 없다 — 메일 채널과 같은 판단이다. */}
        <span className="text-muted">
          {provider.scim_last_seen_at
            ? t('admin:scim.lastSeen', { when: formatDateTime(provider.scim_last_seen_at) })
            : t('admin:scim.neverSeen')}
        </span>
        <Button
          type="button"
          variant="ghost"
          className="ml-auto text-xs"
          loading={issue.isPending}
          onClick={() => { issue.mutate() }}
        >
          {provider.scim_has_token ? t('admin:scim.reissue') : t('admin:scim.issue')}
        </Button>
        {provider.scim_enabled ? (
          <Button
            type="button"
            variant="ghost"
            className="text-xs"
            loading={stop.isPending}
            onClick={() => { stop.mutate() }}
          >
            {t('admin:scim.stop')}
          </Button>
        ) : null}
      </div>

      {issue.isError ? <Alert>{describeError(issue.error)}</Alert> : null}
      {stop.isError ? <Alert>{describeError(stop.error)}</Alert> : null}

      {issued !== null ? (
        <div className="flex flex-col gap-1 rounded-md border border-border bg-surface p-2">
          <p className="text-muted">{t('admin:scim.tokenOnce')}</p>
          {/* 고르기 쉬워야 한다. IdP 설정 화면에 붙여 넣을 값이다. */}
          <code className="break-all font-mono text-xs text-fg">{issued}</code>
          <p className="text-muted">
            {t('admin:scim.endpoint', { url: `${location.origin}/scim/v2` })}
          </p>
        </div>
      ) : null}
    </div>
  )
}
